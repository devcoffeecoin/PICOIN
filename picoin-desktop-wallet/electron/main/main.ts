import path from "node:path";
import { app, BrowserWindow, ipcMain, shell } from "electron";
import type { AppSettings, NetworkId, SendTransactionRequest } from "../../shared/types";
import { allNetworks, networkConfig } from "./config/networks";
import { PicoinAPI } from "./services/PicoinAPI";
import { SettingsStore } from "./services/SettingsStore";
import { WalletService } from "./services/WalletService";

let mainWindow: BrowserWindow | null = null;
let apiClient: PicoinAPI;
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

function selectedNetworkConfig() {
  const settings = settingsStore.get();
  return networkConfig(settings.selectedNetwork, settings.apiUrls[settings.selectedNetwork]);
}

function refreshApiNetwork(): void {
  apiClient.setNetwork(selectedNetworkConfig());
}

function registerIpc(): void {
  ipcMain.handle("app:get-settings", () => settingsStore.get());
  ipcMain.handle("app:update-settings", (_event, patch: Partial<AppSettings>) => {
    const next = settingsStore.update(patch);
    refreshApiNetwork();
    return next;
  });
  ipcMain.handle("app:set-network", (_event, network: NetworkId) => {
    const next = settingsStore.setNetwork(network);
    refreshApiNetwork();
    return next;
  });
  ipcMain.handle("app:get-networks", () => allNetworks(settingsStore.get().apiUrls));

  ipcMain.handle("api:get-status", () => apiClient.getApiStatus());
  ipcMain.handle("api:get-balance", (_event, address: string) => apiClient.getBalance(address));
  ipcMain.handle("api:get-history", (_event, address: string) => apiClient.getTransactionHistory(address));
  ipcMain.handle("api:get-peers", () => apiClient.getPeers());
  ipcMain.handle("api:get-sync-status", () => apiClient.getSyncStatus());

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
  ipcMain.handle("wallet:import-json", (_event, walletJson: string, password: string) =>
    walletService.importFromJson(walletJson, password, selectedNetworkConfig()),
  );
  ipcMain.handle("wallet:export-keystore", (_event, password: string) => walletService.exportKeystore(password));
  ipcMain.handle("wallet:unlock", (_event, password: string) => walletService.unlockWallet(password));
  ipcMain.handle("wallet:lock", () => walletService.lockWallet());
  ipcMain.handle("wallet:send", async (_event, request: SendTransactionRequest) => {
    const address = walletService.getAddress();
    if (!address) {
      throw new Error("wallet has not been created or imported");
    }
    const network = selectedNetworkConfig();
    const nonce = await apiClient.getNextNonce(address);
    const signedTx = walletService.signTransaction(
      {
        to: request.to,
        amount: request.amount,
        fee: request.fee,
        nonce,
      },
      network,
    );
    return apiClient.sendTransaction(signedTx);
  });
}

app.whenReady().then(() => {
  settingsStore = new SettingsStore();
  apiClient = new PicoinAPI(selectedNetworkConfig());
  walletService = new WalletService();
  registerIpc();
  mainWindow = createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      mainWindow = createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
