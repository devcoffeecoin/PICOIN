export type MinerConfig = {
  apiUrl: string;
  minerName: string;
  rewardAddress: string;
  speed: number;
};

export type MinerIdentity = {
  miner_id: string;
  name: string;
  public_key: string;
  private_key: string;
  reward_address?: string;
  server_url?: string;
};

export type MinerTask = {
  task_id: string;
  range_start: number;
  range_end: number;
  algorithm: string;
  status?: string;
  chain_id?: string | number;
  network_id?: string;
  tx_merkle_root?: string;
  mempool_snapshot_id?: string;
  selected_tx_hashes_hash?: string;
  tx_count?: number;
  tx_fee_total_units?: number;
};

export type MinerStatus = {
  connected: boolean;
  message: string;
  task?: MinerTask;
  lastBlock?: string;
  accepted?: number;
  rejected?: number;
};

declare global {
  interface Window {
    minerApi: {
      request: (method: string, url: string, body?: unknown) => Promise<unknown>;
    };
  }
}
