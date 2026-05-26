import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("minerApi", {
  request: async (method: string, url: string, body?: unknown) => {
    return await ipcRenderer.invoke("miner-api-request", {
      method,
      url,
      body,
    });
  },

  start: async (config: unknown) => {
    return await ipcRenderer.invoke("miner-start", config);
  },

  register: async (config: unknown) => {
  return await ipcRenderer.invoke("miner-register", config);
  },

  stop: async () => {
    return await ipcRenderer.invoke("miner-stop");
  },

  status: async () => {
    return await ipcRenderer.invoke("miner-status");
  },

  identity: async () => {
    return await ipcRenderer.invoke("miner-identity");
  },
});
