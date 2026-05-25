import path from "node:path";
import { app, BrowserWindow, ipcMain, shell } from "electron";
import type { AppSettings, NetworkId, SendTransactionRequest } from "../../shared/types";
import { allNetworks, networkConfig } from "./config/networks";
import { NodeManager } from "./services/NodeManager";
import { SettingsStore } from "./services/SettingsStore";
import { WalletService } from "./services/WalletService";

let mainWindow: BrowserWindow | null = null;
let nodeManager: NodeManager;
let settingsStore: SettingsStore;
let walletService: WalletService;

function createWindow(): BrowserWindow {
  const preloadPath = path.join(__dirname, "../preload/preload.js");
  const iconPath = app.isPackaged
    ? path.join(process.resourcesPath, "icons", "picoin-logo.png")
    : path.join(app.getAppPath(), "resources", "icons", "picoin-logo.png");
  const window = new BrowserWindow({
    width: 1240,
    height: 820,
    minWidth: 1080,
    minHeight: 720,
    backgroundColor: "#080b12",
    title: "Picoin Wallet",
    icon: iconPath,
    webPreferences: {
      preload: preloadPath,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
      devTools: !app.isPackaged,
    },
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });

  const devServerUrl = process.env.VITE_DEV_SERVER_URL;
  if (devServerUrl) {
    void window.loadURL(devServerUrl);
  } else {
    void window.loadFile(path.join(app.getAppPath(), "dist", "index.html"));
  }
  return window;
}

async function startConfiguredNode(): Promise<void> {
  const settings = settingsStore.get();
  const network = networkConfig(settings.selectedNetwork, settings.dataDirs[settings.selectedNetwork]);
  await nodeManager.start(settings.nodePath, network);
}

function selectedNetworkConfig() {
  const settings = settingsStore.get();
  return networkConfig(settings.selectedNetwork, settings.dataDirs[settings.selectedNetwork]);
}

function registerIpc(): void {
  ipcMain.handle("app:get-settings", () => settingsStore.get());
  ipcMain.handle("app:update-settings", async (_event, patch: Partial<AppSettings>) => {
    const before = settingsStore.get();
    const next = settingsStore.update(patch);
    const networkChanged = before.selectedNetwork !== next.selectedNetwork;
    const nodePathChanged = before.nodePath !== next.nodePath;
    const dataDirChanged = JSON.stringify(before.dataDirs) !== JSON.stringify(next.dataDirs);
    if (networkChanged || nodePathChanged || dataDirChanged) {
      await nodeManager.restart(next.nodePath, networkConfig(next.selectedNetwork, next.dataDirs[next.selectedNetwork]));
    }
    return next;
  });
  ipcMain.handle("app:set-network", async (_event, network: NetworkId) => {
    const next = settingsStore.setNetwork(network);
    await nodeManager.restart(next.nodePath, networkConfig(next.selectedNetwork, next.dataDirs[next.selectedNetwork]));
    return next;
  });
  ipcMain.handle("app:get-networks", () => allNetworks(settingsStore.get().dataDirs));

  ipcMain.handle("node:get-status", () => nodeManager.getStatus());
  ipcMain.handle("node:start", async () => {
    const settings = settingsStore.get();
    return nodeManager.start(settings.nodePath, selectedNetworkConfig());
  });
  ipcMain.handle("node:stop", async () => nodeManager.stop());
  ipcMain.handle("node:refresh", async () => nodeManager.refreshStatus());

  ipcMain.handle("rpc:get-balance", async (_event, address: string) => nodeManager.getRpc().getBalance(address));
  ipcMain.handle("rpc:get-history", async (_event, address: string) => nodeManager.getRpc().getTransactionHistory(address));
  ipcMain.handle("rpc:get-peers", async () => nodeManager.getRpc().getPeers());
  ipcMain.handle("rpc:get-sync-status", async () => nodeManager.getRpc().getSyncStatus());

  ipcMain.handle("wallet:get-summary", () => walletService.getSummary());
  ipcMain.handle("wallet:create", (_event, password: string) =>
    walletService.createWallet(password, selectedNetworkConfig()),
  );
  ipcMain.handle("wallet:import-seed", (_event, seedPhrase: string, password: string) =>
    walletService.importFromSeed(seedPhrase, password, selectedNetworkConfig()),
  );
  ipcMain.handle("wallet:import-private-key", (_event, privateKey: string, password: string) =>
    walletService.importFromPrivateKey(privateKey, password, selectedNetworkConfig()),
  );
  ipcMain.handle("wallet:export-keystore", (_event, password: string) => walletService.exportKeystore(password));
  ipcMain.handle("wallet:unlock", (_event, password: string) => walletService.unlockWallet(password));
  ipcMain.handle("wallet:lock", () => walletService.lockWallet());
  ipcMain.handle("wallet:send", async (_event, request: SendTransactionRequest) => {
    const address = walletService.getAddress();
    if (!address) {
      throw new Error("wallet has not been created or imported");
    }
    const nonce = await nodeManager.getRpc().getNextNonce(address);
    const signedTx = walletService.signTransaction(
      {
        to: request.to,
        amount: request.amount,
        fee: request.fee,
        nonce,
      },
      selectedNetworkConfig(),
    );
    return nodeManager.getRpc().sendTransaction(signedTx);
  });
}

app.whenReady().then(async () => {
  settingsStore = new SettingsStore();
  const settings = settingsStore.get();
  nodeManager = new NodeManager(settings.nodePath, networkConfig(settings.selectedNetwork, settings.dataDirs[settings.selectedNetwork]));
  walletService = new WalletService();
  registerIpc();
  mainWindow = createWindow();
  await startConfiguredNode();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow();
    }
  });
});

app.on("before-quit", async (event) => {
  if (!nodeManager) {
    return;
  }
  event.preventDefault();
  await nodeManager.stop();
  app.exit(0);
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
