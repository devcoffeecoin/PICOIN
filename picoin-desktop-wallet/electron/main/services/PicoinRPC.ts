import type {
  AccountBalance,
  PeerInfo,
  SendTransactionResult,
  SignedTransaction,
  SyncStatus,
  TransactionRecord,
} from "../../../shared/types";

export class PicoinRPC {
  constructor(private rpcUrl: string) {}

  setRpcUrl(rpcUrl: string): void {
    this.rpcUrl = rpcUrl.replace(/\/$/, "");
  }

  async isAvailable(timeoutMs = 1500): Promise<boolean> {
    try {
      await this.request("/node/sync-status", { timeoutMs });
      return true;
    } catch {
      return false;
    }
  }

  async getBlockHeight(): Promise<number | null> {
    const status = await this.getSyncStatus();
    return status.blockHeight;
  }

  async getSyncStatus(): Promise<SyncStatus> {
    const raw = await this.request<Record<string, unknown>>("/node/sync-status");
    const replay = raw.replay && typeof raw.replay === "object" ? (raw.replay as Record<string, unknown>) : {};
    const localBlockHeight = numberOrNull(raw.local_block_height);
    const latestBlockHeight = numberOrNull(raw.latest_block_height ?? raw.effective_latest_block_height);
    return {
      blockHeight: localBlockHeight ?? latestBlockHeight,
      localBlockHeight,
      latestBlockHeight,
      status: String(raw.sync_status || replay.sync_status || "unknown"),
      raw,
    };
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
      // TODO: adapt this when the final node peer endpoint is frozen.
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
      symbol: "PI",
      raw,
    };
  }

  async getTransactionHistory(address: string): Promise<TransactionRecord[]> {
    try {
      const raw = await this.request<unknown>(`/accounts/${encodeURIComponent(address)}/history?limit=50`);
      if (Array.isArray(raw)) {
        return raw as TransactionRecord[];
      }
      if (raw && typeof raw === "object" && Array.isArray((raw as { transactions?: unknown[] }).transactions)) {
        return (raw as { transactions: TransactionRecord[] }).transactions;
      }
      return [];
    } catch (error) {
      // TODO: connect to the canonical explorer/history endpoint if this route changes.
      if (isHttpStatus(error, 404)) {
        return [];
      }
      throw error;
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
    // The wallet service signs; RPC only broadcasts. Kept as a named adapter for the V1 API.
    return this.broadcastTransaction(rawTx);
  }

  async getNextNonce(address: string): Promise<number> {
    const raw = await this.request<Record<string, unknown>>(`/wallet/${encodeURIComponent(address)}/nonce`);
    const nextNonce = Number(raw.next_nonce);
    if (!Number.isInteger(nextNonce) || nextNonce < 1) {
      throw new Error("RPC nonce endpoint returned an invalid next_nonce");
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
      const response = await fetch(`${this.rpcUrl}${pathname}`, {
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

function isHttpStatus(error: unknown, status: number): boolean {
  return error instanceof HttpError && error.status === status;
}

function numberOrNull(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
