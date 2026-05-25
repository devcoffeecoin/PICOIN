"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const path_1 = __importDefault(require("path"));
const electron_1 = require("electron");
const isDev = process.env.VITE_DEV_SERVER_URL !== undefined;
function createWindow() {
    const win = new electron_1.BrowserWindow({
        width: 1280,
        height: 800,
        minWidth: 1080,
        minHeight: 720,
        show: false,
        webPreferences: {
            preload: path_1.default.join(__dirname, "..", "preload", "preload.js"),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });
    win.on("ready-to-show", () => win.show());
    if (isDev && process.env.VITE_DEV_SERVER_URL) {
        win.loadURL(process.env.VITE_DEV_SERVER_URL);
    }
    else {
        const indexPath = path_1.default.join(__dirname, "..", "..", "..", "dist", "index.html");
        win.loadFile(indexPath).catch((error) => {
            console.error("Failed to load index.html:", error);
        });
    }
    win.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL) => {
        console.error(`Load failed ${errorCode}: ${errorDescription} (${validatedURL})`);
        win.show();
    });
}
async function makeApiRequest(method, url, body) {
    const headers = { "Accept": "application/json" };
    const options = { method, headers };
    if (body !== undefined && body !== null) {
        headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    const text = await response.text();
    if (!response.ok) {
        throw new Error(`HTTP ${response.status} ${response.statusText}: ${text}`);
    }
    try {
        return JSON.parse(text);
    }
    catch {
        return text;
    }
}
electron_1.app.whenReady().then(() => {
    electron_1.ipcMain.handle("miner-api-request", async (_, request) => {
        return await makeApiRequest(request.method, request.url, request.body);
    });
    createWindow();
    electron_1.app.on("activate", () => {
        if (electron_1.BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
    });
});
electron_1.app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
        electron_1.app.quit();
    }
});
