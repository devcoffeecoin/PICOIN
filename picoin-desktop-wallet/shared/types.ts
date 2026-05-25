export type NetworkId = "testnet" | "mainnet";

export type ApiRuntimeStatus = "online" | "syncing" | "synced" | "offline" | "error";

export interface NetworkConfig {
  id: NetworkId;
  apiUrl: string;
  chainName: string;
  symbol: "PI";
  network: string;
  chainId: string | number;
}

export interface AppSettings {
  selectedNetwork: NetworkId;
  apiUrls: Record<NetworkId, string>;
}

export interface ApiStatus {
  status: ApiRuntimeStatus;
  network: NetworkId;
  apiUrl: string;
  blockHeight: number | null;
  syncStatus: string;
  message?: string;
  checkedAt?: string;
}

export interface SyncStatus {
  blockHeight: number | null;
  localBlockHeight: number | null;
  latestBlockHeight: number | null;
  status: string;
  raw: unknown;
}

export interface PeerInfo {
  peer_id?: string;
  peer_address?: string;
  status?: string;
  [key: string]: unknown;
}

export interface AccountBalance {
  address: string;
  balance: number;
  symbol: "PI";
  raw: unknown;
}

export interface TransactionRecord {
  tx_hash?: string;
  tx_type?: string;
  sender?: string;
  recipient?: string | null;
  amount?: number;
  fee?: number;
  status?: string;
  timestamp?: string;
  confirmed_at?: string | null;
  [key: string]: unknown;
}

export interface WalletSummary {
  hasWallet: boolean;
  locked: boolean;
  address: string | null;
  publicKey: string | null;
  network?: string;
  chainId?: string | number;
}

export interface CreateWalletResult extends WalletSummary {
  seedPhrase: string;
}

export interface SendTransactionRequest {
  to: string;
  amount: number;
  fee: number;
}

export interface SendTransactionResult {
  txHash: string | null;
  raw: unknown;
}

export interface KeystoreExport {
  fileName: string;
  keystore: string;
}

export interface TransactionDraft {
  tx_type: string;
  sender: string;
  recipient: string | null;
  amount: string;
  amount_units: number;
  fee: string;
  fee_units: number;
  nonce: number;
  payload: Record<string, unknown>;
  network_id: string;
  chain_id: string | number;
  timestamp: string;
}

export interface SignedTransaction extends TransactionDraft {
  public_key: string;
  signature: string;
  tx_hash: string;
}
