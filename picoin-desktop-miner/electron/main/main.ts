import path from "path";
import { app, BrowserWindow, ipcMain } from "electron";

const isDev = process.env.VITE_DEV_SERVER_URL !== undefined;

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 1080,
    minHeight: 720,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.on("ready-to-show", () => win.show());

  if (isDev && process.env.VITE_DEV_SERVER_URL) {
    win.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    const indexPath = path.join(__dirname, "..", "..", "..", "dist", "index.html");
    win.loadFile(indexPath).catch((error) => {
      console.error("Failed to load index.html:", error);
    });
  }

  win.webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedURL) => {
    console.error(`Load failed ${errorCode}: ${errorDescription} (${validatedURL})`);
    win.show();
  });
}

async function makeApiRequest(method: string, url: string, body?: any) {
  const headers: Record<string, string> = { "Accept": "application/json" };
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

app.whenReady().then(() => {
  ipcMain.handle("miner-api-request", async (_, request) => {
    return await makeApiRequest(request.method, request.url, request.body);
  });

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
