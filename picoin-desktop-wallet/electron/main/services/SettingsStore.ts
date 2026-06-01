import fs from "node:fs";
import path from "node:path";
import { app } from "electron";
import type { AppSettings, NetworkId } from "../../../shared/types";
import { DEFAULT_API_URLS, normalizeApiUrl } from "../config/networks";

const SETTINGS_FILE = "settings.json";
const LEGACY_TESTNET_API_URLS = new Set([
  "https://api.picoin.science",
  "https://testnet-api.picoin.science",
]);
const LEGACY_MAINNET_API_URL = "https://mainnet-api.picoin.science";

function defaultSettings(): AppSettings {
  return {
    selectedNetwork: "mainnet",
    apiUrls: DEFAULT_API_URLS,
  };
}

export class SettingsStore {
  private readonly settingsPath: string;

  constructor() {
    this.settingsPath = path.join(app.getPath("userData"), SETTINGS_FILE);
  }

  get(): AppSettings {
    const fallback = defaultSettings();
    if (!fs.existsSync(this.settingsPath)) {
      return fallback;
    }
    try {
      const parsed = JSON.parse(fs.readFileSync(this.settingsPath, "utf-8")) as Partial<AppSettings> & {
        rpcUrls?: Partial<Record<NetworkId, string>>;
      };
      const savedApiUrls = parsed.apiUrls || parsed.rpcUrls || {};
      const selectedNetwork =
        parsed.selectedNetwork === "testnet" || parsed.selectedNetwork === "mainnet"
          ? parsed.selectedNetwork
          : fallback.selectedNetwork;
      const savedTestnetApi = savedApiUrls.testnet ? normalizeApiUrl(savedApiUrls.testnet) : "";
      const savedMainnetApi = savedApiUrls.mainnet ? normalizeApiUrl(savedApiUrls.mainnet) : "";
      const legacyTestnetSelection =
        selectedNetwork === "testnet" &&
        (!savedTestnetApi || LEGACY_TESTNET_API_URLS.has(savedTestnetApi)) &&
        (!savedMainnetApi || savedMainnetApi === LEGACY_MAINNET_API_URL);
      return {
        selectedNetwork: legacyTestnetSelection ? "mainnet" : selectedNetwork,
        apiUrls: {
          testnet: normalizeApiUrl(savedApiUrls.testnet || fallback.apiUrls.testnet),
          mainnet: normalizeApiUrl(
            savedMainnetApi === LEGACY_MAINNET_API_URL
              ? fallback.apiUrls.mainnet
              : savedApiUrls.mainnet || fallback.apiUrls.mainnet,
          ),
        },
      };
    } catch {
      return fallback;
    }
  }

  update(patch: Partial<AppSettings>): AppSettings {
    const current = this.get();
    const next = {
      ...current,
      ...patch,
      apiUrls: {
        testnet: normalizeApiUrl((patch.apiUrls?.testnet || current.apiUrls.testnet).trim()),
        mainnet: normalizeApiUrl((patch.apiUrls?.mainnet || current.apiUrls.mainnet).trim()),
      },
    };
    fs.mkdirSync(path.dirname(this.settingsPath), { recursive: true });
    fs.writeFileSync(this.settingsPath, JSON.stringify(next, null, 2), "utf-8");
    return next;
  }

  setNetwork(network: NetworkId): AppSettings {
    return this.update({ selectedNetwork: network });
  }
}
