import { contextBridge, ipcRenderer } from "electron";

type ApiRequest = {
  method: string;
  url: string;
  body?: unknown;
};

type ApiResponse = unknown;

contextBridge.exposeInMainWorld("minerApi", {
  request: async (method: string, url: string, body?: unknown): Promise<ApiResponse> => {
    return await ipcRenderer.invoke("miner-api-request", { method, url, body } as ApiRequest);
  },
});
