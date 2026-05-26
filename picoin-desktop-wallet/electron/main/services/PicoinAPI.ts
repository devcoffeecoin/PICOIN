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
      const raw = await this.request<Record<string, unknown>>("/node/sync-status", { timeoutMs: 5000 });
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
      const protocol = await this.request<Record<string, unknown>>("/protocol", { timeoutMs: 5000 });
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
    const raw = await this.request<Record<string, unknown>>(`/accounts/${encodeURIComponent(address)}`);
    return {
      address,
      balance: Number(raw.balance ?? 0),
      symbol: this.network.symbol,
      raw,
    };
  }

  async getTransactionHistory(address: string): Promise<TransactionRecord[]> {
    try {
      const raw = await this.request<unknown>(`/accounts/${encodeURIComponent(address)}/history?limit=50`);
      return normalizeTransactions(raw);
    } catch (error) {
      if (!isHttpStatus(error, 404)) {
        throw error;
      }
      // TODO: switch to the canonical account history endpoint once it is frozen.
      const recent = await this.request<unknown>("/transactions/recent?limit=50");
      return normalizeTransactions(recent).filter((tx) => tx.sender === address || tx.recipient === address);
    }
  }

  async broadcastTransaction(rawTx: SignedTransaction): Promise<SendTransactionResult> {
    const raw = await this.request<Record<string, unknown>>("/transactions/submit", {
      method: "POST",
      body: rawTx,
    });
    return {
      txHash: String(raw.tx_hash || raw.txHash || rawTx.tx_hash || ""),
      raw,
    };
  }

  async sendTransaction(rawTx: SignedTransaction): Promise<SendTransactionResult> {
    return this.broadcastTransaction(rawTx);
  }

  async getNextNonce(address: string): Promise<number> {
    const raw = await this.request<Record<string, unknown>>(`/wallet/${encodeURIComponent(address)}/nonce`);
    const nextNonce = Number(raw.next_nonce);
    if (!Number.isInteger(nextNonce) || nextNonce < 1) {
      throw new Error("API nonce endpoint returned an invalid next_nonce");
    }
    return nextNonce;
  }

  private async request<T>(
    pathname: string,
    options: {
      method?: "GET" | "POST";
      body?: unknown;
      timeoutMs?: number;
    } = {},
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? 8000);
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
    } finally {
      clearTimeout(timer);
    }
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

function numberOrNull(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error || "Unknown error");
}
