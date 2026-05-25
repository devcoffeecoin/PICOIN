import { contextBridge, ipcRenderer } from "electron";
import type {
  AppSettings,
  CreateWalletResult,
  NetworkConfig,
  NetworkId,
  NodeStatus,
  SendTransactionRequest,
  SendTransactionResult,
  WalletSummary,
} from "../../shared/types";

const api = {
  settings: {
    get: () => ipcRenderer.invoke("app:get-settings") as Promise<AppSettings>,
    update: (patch: Partial<AppSettings>) => ipcRenderer.invoke("app:update-settings", patch) as Promise<AppSettings>,
    setNetwork: (network: NetworkId) => ipcRenderer.invoke("app:set-network", network) as Promise<AppSettings>,
    networks: () => ipcRenderer.invoke("app:get-networks") as Promise<Record<NetworkId, NetworkConfig>>,
  },
  node: {
    status: () => ipcRenderer.invoke("node:get-status") as Promise<NodeStatus>,
    start: () => ipcRenderer.invoke("node:start") as Promise<NodeStatus>,
    stop: () => ipcRenderer.invoke("node:stop") as Promise<NodeStatus>,
    refresh: () => ipcRenderer.invoke("node:refresh") as Promise<NodeStatus>,
  },
  rpc: {
    getBalance: (address: string) => ipcRenderer.invoke("rpc:get-balance", address),
    getHistory: (address: string) => ipcRenderer.invoke("rpc:get-history", address),
    getPeers: () => ipcRenderer.invoke("rpc:get-peers"),
    getSyncStatus: () => ipcRenderer.invoke("rpc:get-sync-status"),
  },
  wallet: {
    summary: () => ipcRenderer.invoke("wallet:get-summary") as Promise<WalletSummary>,
    create: (password: string) => ipcRenderer.invoke("wallet:create", password) as Promise<CreateWalletResult>,
    importSeed: (seedPhrase: string, password: string) =>
      ipcRenderer.invoke("wallet:import-seed", seedPhrase, password) as Promise<WalletSummary>,
    importPrivateKey: (privateKey: string, password: string) =>
      ipcRenderer.invoke("wallet:import-private-key", privateKey, password) as Promise<WalletSummary>,
    exportKeystore: (password: string) => ipcRenderer.invoke("wallet:export-keystore", password),
    unlock: (password: string) => ipcRenderer.invoke("wallet:unlock", password) as Promise<WalletSummary>,
    lock: () => ipcRenderer.invoke("wallet:lock") as Promise<WalletSummary>,
    send: (request: SendTransactionRequest) =>
      ipcRenderer.invoke("wallet:send", request) as Promise<SendTransactionResult>,
  },
};

contextBridge.exposeInMainWorld("picoin", api);

export type PicoinDesktopAPI = typeof api;

