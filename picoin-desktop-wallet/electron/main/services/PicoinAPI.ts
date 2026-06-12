import type {
  AccountBalance,
  ApiStatus,
  NetworkConfig,
  PeerInfo,
  SendTransactionResult,
  SignedTransaction,
  SyncStatus,
  TransactionRecord,
} from "../../../shared/types";

const DEFAULT_READ_TIMEOUT_MS = 20_000;
const STATUS_TIMEOUT_MS = 12_000;
const WALLET_READ_TIMEOUT_MS = 45_000;
const NONCE_TIMEOUT_MS = 90_000;
const TX_SUBMIT_TIMEOUT_MS = 120_000;
const TX_VERIFY_TIMEOUT_MS = 15_000;
const RETRY_DELAY_MS = 750;
const SUBMITTED_TX_STATUSES = new Set(["pending", "propagated", "selected", "released", "confirmed"]);

export class PicoinAPI {
  private network: NetworkConfig;

  constructor(network: NetworkConfig) {
    this.network = network;
  }

  setNetwork(network: NetworkConfig): void {
    this.network = network;
  }

  async getApiStatus(): Promise<ApiStatus> {
    try {
      const sync = await this.getSyncStatus();
      const status = sync.status === "healthy" || sync.status === "synced" ? "synced" : "online";
      return {
        status,
        network: this.network.id,
        apiUrl: this.network.apiUrl,
        blockHeight: sync.blockHeight,
        syncStatus: sync.status,
        checkedAt: new Date().toISOString(),
      };
    } catch (error) {
      return {
        status: "offline",
        network: this.network.id,
        apiUrl: this.network.apiUrl,
        blockHeight: null,
        syncStatus: "unavailable",
        message: errorMessage(error),
        checkedAt: new Date().toISOString(),
      };
    }
  }

  async getBlockHeight(): Promise<number | null> {
    const status = await this.getSyncStatus();
    return status.blockHeight;
  }

  async getSyncStatus(): Promise<SyncStatus> {
    try {
      const raw = await this.request<Record<string, unknown>>("/node/sync-status", {
        timeoutMs: STATUS_TIMEOUT_MS,
        retries: 1,
      });
      const replay = raw.replay && typeof raw.replay === "object" ? (raw.replay as Record<string, unknown>) : {};
      const localBlockHeight = numberOrNull(raw.local_block_height);
      const latestBlockHeight = numberOrNull(raw.latest_block_height ?? raw.effective_latest_block_height);
      return {
        blockHeight: localBlockHeight ?? latestBlockHeight,
        localBlockHeight,
        latestBlockHeight,
        status: String(raw.sync_status || replay.sync_status || "online"),
        raw,
      };
    } catch (error) {
      if (!isHttpStatus(error, 404)) {
        throw error;
      }
      // TODO: replace this fallback once the public API exposes a canonical wallet status endpoint.
      const protocol = await this.request<Record<string, unknown>>("/protocol", {
        timeoutMs: STATUS_TIMEOUT_MS,
        retries: 1,
      });
      return {
        blockHeight: numberOrNull(protocol.latest_block_height ?? protocol.block_height),
        localBlockHeight: null,
        latestBlockHeight: numberOrNull(protocol.latest_block_height ?? protocol.block_height),
        status: "online",
        raw: protocol,
      };
    }
  }

  async getPeers(): Promise<PeerInfo[]> {
    try {
      const raw = await this.request<unknown>("/node/peers");
      if (Array.isArray(raw)) {
        return raw as PeerInfo[];
      }
      if (raw && typeof raw === "object" && Array.isArray((raw as { peers?: unknown[] }).peers)) {
        return (raw as { peers: PeerInfo[] }).peers;
      }
      return [];
    } catch (error) {
      // Wallet V1 does not depend on peer data; this endpoint is informational only.
      if (isHttpStatus(error, 404)) {
        return [];
      }
      throw error;
    }
  }

  async getBalance(address: string): Promise<AccountBalance> {
    const encodedAddress = encodeURIComponent(address);
    const raw = await this.requestFirst<Record<string, unknown>>([
      `/wallet/balance/${encodedAddress}`,
      `/accounts/${encodedAddress}`,
    ], { timeoutMs: WALLET_READ_TIMEOUT_MS, retries: 1 });
    return {
      address,
      balance: Number(raw.balance ?? 0),
      symbol: this.network.symbol,
      raw,
    };
  }

  async getTransactionHistory(address: string): Promise<TransactionRecord[]> {
    const encodedAddress = encodeURIComponent(address);
    const raw = await this.requestFirst<unknown>([
      `/transactions/${encodedAddress}`,
      `/accounts/${encodedAddress}/history?limit=50`,
      "/transactions/recent?limit=50",
    ], { timeoutMs: WALLET_READ_TIMEOUT_MS, retries: 1 });
    return normalizeTransactions(raw).filter((tx) => {
      if (tx.sender === address || tx.recipient === address) {
        return true;
      }
      return !tx.sender && !tx.recipient;
    });
  }

  async broadcastTransaction(rawTx: SignedTransaction): Promise<SendTransactionResult> {
    const endpoints = ["/tx/send", "/transactions/submit", "/tx/submit"];
    let lastError: unknown = null;
    for (const endpoint of endpoints) {
      try {
        const raw = await this.request<Record<string, unknown>>(endpoint, {
          method: "POST",
          body: rawTx,
          timeoutMs: TX_SUBMIT_TIMEOUT_MS,
          retries: 1,
        });
        const txHash = String(raw.tx_hash || raw.txHash || rawTx.tx_hash || "");
        const verified = txHash ? await this.findSubmittedTransaction(txHash) : null;
        return {
          txHash,
          status: String(verified?.status || raw.status || "submitted"),
          verified: Boolean(verified),
          raw: verified || raw,
        };
      } catch (error) {
        lastError = error;
        const recovered = await this.findSubmittedTransaction(rawTx.tx_hash);
        if (recovered) {
          return {
            txHash: rawTx.tx_hash,
            status: String(recovered.status || "submitted"),
            verified: true,
            raw: {
              ...recovered,
              recovered_after_submit_error: true,
              submit_error: errorMessage(error),
            },
          };
        }
        if (!canTryNextEndpoint(error)) {
          throw error;
        }
      }
    }
    throw lastError || new Error("transaction submit failed");
  }

  async sendTransaction(rawTx: SignedTransaction): Promise<SendTransactionResult> {
    return this.broadcastTransaction(rawTx);
  }

  async getNextNonce(address: string): Promise<number> {
    const raw = await this.request<Record<string, unknown>>(`/wallet/${encodeURIComponent(address)}/nonce`, {
      timeoutMs: NONCE_TIMEOUT_MS,
      retries: 2,
    });
    const nextNonce = Number(raw.next_nonce);
    if (!Number.isInteger(nextNonce) || nextNonce < 1) {
      throw new Error("API nonce endpoint returned an invalid next_nonce");
    }
    return nextNonce;
  }

  private async findSubmittedTransaction(txHash: string): Promise<Record<string, unknown> | null> {
    for (let attempt = 0; attempt < 5; attempt += 1) {
      try {
        const raw = await this.request<Record<string, unknown>>(`/tx/${encodeURIComponent(txHash)}`, {
          timeoutMs: TX_VERIFY_TIMEOUT_MS,
          retries: 1,
        });
        const status = String(raw.status || "");
        if (SUBMITTED_TX_STATUSES.has(status)) {
          return raw;
        }
      } catch (error) {
        if (!canTryNextEndpoint(error)) {
          return null;
        }
      }
      await delay(750 * (attempt + 1));
    }
    return null;
  }

  private async request<T>(
    pathname: string,
    options: {
      method?: "GET" | "POST";
      body?: unknown;
      timeoutMs?: number;
      retries?: number;
    } = {},
  ): Promise<T> {
    let lastError: unknown = null;
    const attempts = Math.max(1, (options.retries ?? 0) + 1);
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_READ_TIMEOUT_MS);
      try {
        const response = await fetch(`${this.network.apiUrl}${pathname}`, {
          method: options.method ?? "GET",
          headers: options.body ? { "content-type": "application/json" } : undefined,
          body: options.body ? JSON.stringify(options.body) : undefined,
          signal: controller.signal,
        });
        if (!response.ok) {
          const message = await response.text().catch(() => "");
          throw new HttpError(response.status, message || response.statusText);
        }
        return (await response.json()) as T;
      } catch (error) {
        lastError = error;
        if (attempt >= attempts - 1 || !isTransientError(error)) {
          throw error;
        }
        await delay(RETRY_DELAY_MS * (attempt + 1));
      } finally {
        clearTimeout(timer);
      }
    }
    throw lastError || new Error("API request failed");
  }

  private async requestFirst<T>(
    pathnames: string[],
    options: {
      timeoutMs?: number;
      retries?: number;
    } = {},
  ): Promise<T> {
    let lastError: unknown = null;
    for (const pathname of pathnames) {
      try {
        return await this.request<T>(pathname, {
          timeoutMs: options.timeoutMs ?? DEFAULT_READ_TIMEOUT_MS,
          retries: options.retries,
        });
      } catch (error) {
        lastError = error;
        if (!canTryNextEndpoint(error)) {
          throw error;
        }
      }
    }
    throw lastError || new Error("API endpoint unavailable");
  }
}

class HttpError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function normalizeTransactions(raw: unknown): TransactionRecord[] {
  if (Array.isArray(raw)) {
    return raw as TransactionRecord[];
  }
  if (raw && typeof raw === "object" && Array.isArray((raw as { transactions?: unknown[] }).transactions)) {
    return (raw as { transactions: TransactionRecord[] }).transactions;
  }
  return [];
}

function isHttpStatus(error: unknown, status: number): boolean {
  return error instanceof HttpError && error.status === status;
}

function canTryNextEndpoint(error: unknown): boolean {
  if (isHttpStatus(error, 404)) {
    return true;
  }
  if (error instanceof HttpError) {
    return error.status === 408 || error.status === 429 || error.status >= 500;
  }
  return isTransientError(error);
}

function isTransientError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  const message = error.message.toLowerCase();
  return error.name === "AbortError" || message.includes("aborted") || message.includes("timeout") || message.includes("fetch failed");
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function numberOrNull(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    if (isTransientError(error)) {
      return "API request timed out";
    }
    return error.message;
  }
  return String(error || "Unknown error");
}
