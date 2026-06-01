import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("validatorApi", {
  request: async (method: string, url: string, body?: unknown) => {
    return ipcRenderer.invoke("validator-api-request", { method, url, body });
  },
  startNode: async (config: unknown) => ipcRenderer.invoke("validator-start-node", config),
  stopNode: async () => ipcRenderer.invoke("validator-stop-node"),
  register: async (config: unknown) => ipcRenderer.invoke("validator-register", config),
  start: async (config: unknown) => ipcRenderer.invoke("validator-start", config),
  stop: async () => ipcRenderer.invoke("validator-stop"),
  status: async () => ipcRenderer.invoke("validator-status"),
  identity: async () => ipcRenderer.invoke("validator-identity"),
  restoreSnapshot: async (config: unknown) => ipcRenderer.invoke("validator-restore-snapshot", config),
  catchUp: async (config: unknown) => ipcRenderer.invoke("validator-catch-up", config),
  chooseWallet: async () => ipcRenderer.invoke("validator-choose-wallet"),
  stake: async (config: unknown) => ipcRenderer.invoke("validator-stake", config),
  unstake: async (config: unknown) => ipcRenderer.invoke("validator-unstake", config),
  updateRewardWallet: async (config: unknown) => ipcRenderer.invoke("validator-update-reward-wallet", config),
});
