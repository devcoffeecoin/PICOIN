"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const electron_1 = require("electron");
electron_1.contextBridge.exposeInMainWorld("minerApi", {
    request: async (method, url, body) => {
        return await electron_1.ipcRenderer.invoke("miner-api-request", { method, url, body });
    },
});
