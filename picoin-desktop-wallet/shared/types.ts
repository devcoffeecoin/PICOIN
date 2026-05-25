export type NetworkId = "testnet" | "mainnet";

export type NodeRuntimeStatus = "stopped" | "starting" | "running" | "syncing" | "synced" | "error";

export interface NetworkConfig {
  id: NetworkId;
  rpcUrl: string;
  chainName: string;
  symbol: "PI";
  dataDir: string;
  nodeArgs: string[];
}

export interface AppSettings {
  selectedNetwork: NetworkId;
  nodePath: string;
  dataDirs: Record<NetworkId, string>;
}

export interface NodeStatus {
  status: NodeRuntimeStatus;
  network: NetworkId;
  rpcUrl: string;
  blockHeight: number | null;
  syncStatus: string;
  peers: number | null;
  message?: string;
  startedAt?: string;
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
  networkId?: string;
  chainId?: string;
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
  chain_id: string;
  timestamp: string;
}

export interface SignedTransaction extends TransactionDraft {
  public_key: string;
  signature: string;
  tx_hash: string;
}

