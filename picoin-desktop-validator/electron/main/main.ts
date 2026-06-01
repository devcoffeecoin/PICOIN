import path from "path";
import { app, BrowserWindow, ipcMain } from "electron";
import { ensurePythonRequirements } from "../python-check";
import {
  catchUpNode,
  chooseWalletFile,
  getSavedValidatorIdentity,
  getValidatorProcessStatus,
  registerValidator,
  restoreSnapshot,
  stakeValidator,
  startNode,
  startValidator,
  stopAll,
  stopNode,
  stopValidator,
  unstakeValidator,
  updateValidatorRewardWallet,
} from "./validatorProcess";

let mainWindow: BrowserWindow | null = null;
const isDev = process.env.VITE_DEV_SERVER_URL !== undefined;

if (!isDev) {
  try {
    if (require("electron-squirrel-startup")) {
      app.quit();
      process.exit(0);
    }
  } catch {
    // Optional dependency in NSIS builds.
  }
}

const instanceLock = app.requestSingleInstanceLock();
if (!instanceLock) {
  app.quit();
  process.exit(0);
}

app.on("second-instance", () => {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
});

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 1080,
    minHeight: 720,
    title: "Picoin Validator",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (isDev) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on("ready-to-show", () => {
    mainWindow?.show();
  });

  if (isDev && process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "..", "..", "dist", "index.html"));
  }
}

async function makeApiRequest(method: string, url: string, body?: unknown) {
  const headers: Record<string, string> = { Accept: "application/json" };
  const options: RequestInit = { method, headers };
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
  } catch {
    return text;
  }
}

async function withPython<T>(handler: (pythonCmd: string) => Promise<T>) {
  const pythonCmd = await ensurePythonRequirements();
  return handler(pythonCmd);
}

app.whenReady().then(() => {
  ipcMain.handle("validator-api-request", async (_event, request) => {
    return await makeApiRequest(request.method, request.url, request.body);
  });

  ipcMain.handle("validator-start-node", async (_event, config) => {
    return withPython((pythonCmd) => startNode({ ...config, pythonCmd }));
  });

  ipcMain.handle("validator-stop-node", async () => {
    return stopNode();
  });

  ipcMain.handle("validator-register", async (_event, config) => {
    return withPython((pythonCmd) => registerValidator({ ...config, pythonCmd }));
  });

  ipcMain.handle("validator-start", async (_event, config) => {
    return withPython((pythonCmd) => startValidator({ ...config, pythonCmd }));
  });

  ipcMain.handle("validator-stop", async () => {
    return stopValidator();
  });

  ipcMain.handle("validator-status", async () => {
    return getValidatorProcessStatus();
  });

  ipcMain.handle("validator-identity", async () => {
    return getSavedValidatorIdentity();
  });

  ipcMain.handle("validator-restore-snapshot", async (_event, config) => {
    return withPython((pythonCmd) => restoreSnapshot({ ...config, pythonCmd }));
  });

  ipcMain.handle("validator-catch-up", async (_event, config) => {
    return withPython((pythonCmd) => catchUpNode({ ...config, pythonCmd }));
  });

  ipcMain.handle("validator-choose-wallet", async () => {
    return chooseWalletFile();
  });

  ipcMain.handle("validator-stake", async (_event, config) => {
    return withPython((pythonCmd) => stakeValidator({ ...config, pythonCmd }));
  });

  ipcMain.handle("validator-unstake", async (_event, config) => {
    return withPython((pythonCmd) => unstakeValidator({ ...config, pythonCmd }));
  });

  ipcMain.handle("validator-update-reward-wallet", async (_event, config) => {
    return withPython((pythonCmd) => updateValidatorRewardWallet({ ...config, pythonCmd }));
  });

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", () => {
  stopAll();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
