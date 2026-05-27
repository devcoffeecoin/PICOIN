import path from "path";
import { app, BrowserWindow, ipcMain } from "electron";
import { ensurePythonRequirements } from "../python-check";
import { startMiner, stopMiner, getMinerStatus, registerMiner, getSavedMinerIdentity } from "./minerProcess";

// 1. Variable global asignada correctamente para el control de instancias
let mainWindow: BrowserWindow | null = null;
const isDev = process.env.VITE_DEV_SERVER_URL !== undefined;

// 2. Manejar instaladores de Windows (Solo en Producción para evitar bloqueos en dev)
if (!isDev) {
  try {
    if (require("electron-squirrel-startup")) {
      app.quit();
      process.exit(0);
    }
  } catch (e) {
    console.error("Squirrel startup check skipped:", e);
  }
}

// 3. Bloquear doble instancia estricta
const instanceLock = app.requestSingleInstanceLock();
if (!instanceLock) {
  app.quit();
  process.exit(0); 
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 1080,
    minHeight: 720,
    show: false,
    webPreferences: {
      // Recuerda verificar que la ruta relativa construida apunte al preload real
      preload: path.join(__dirname, "..", "preload", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Mostrar DevTools solo si estás en desarrollo
  if (isDev) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on("ready-to-show", () => {
    if (mainWindow) mainWindow.show();
  });

  if (isDev && process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    const indexPath = path.join(__dirname, "..", "..", "..", "dist", "index.html");
    mainWindow.loadFile(indexPath);
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

// 4. Ciclo de vida y Handlers IPC
app.whenReady().then(() => {
  ipcMain.handle("miner-api-request", async (_event, request) => {
    return await makeApiRequest(request.method, request.url, request.body);
  });

  ipcMain.handle("miner-start", async (_event, config) => {
    console.log("Checking Python requirements...");
    const pythonCmd = await ensurePythonRequirements();
    console.log("Python OK:", pythonCmd);

    return await startMiner({
      ...config,
      pythonCmd
    });
  });

  ipcMain.handle("miner-stop", async (_event) => {
    return await stopMiner();
  });

  ipcMain.handle("miner-status", async (_event) => {
    return await getMinerStatus();
  });

  ipcMain.handle("miner-identity", async (_event) => {
    return getSavedMinerIdentity();
  });

  ipcMain.handle("miner-register", async (_event, config) => {
    const pythonCmd = await ensurePythonRequirements();
    return registerMiner({
      ...config,
      pythonCmd 
    });
  });

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

// CORRECCIÓN AGREGADA: Matar el proceso Python del minero antes de salir
app.on("before-quit", () => {
  console.log("App closing. Stopping active miner processes cleanly...");
  stopMiner(); 
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});