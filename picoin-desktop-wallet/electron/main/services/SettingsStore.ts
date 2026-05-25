import fs from "node:fs";
import path from "node:path";
import { app } from "electron";
import type { AppSettings, NetworkId } from "../../../shared/types";
import { defaultNodePath } from "../config/networks";

const SETTINGS_FILE = "settings.json";

function defaultSettings(): AppSettings {
  const root = app.getPath("userData");
  return {
    selectedNetwork: "testnet",
    nodePath: defaultNodePath(),
    dataDirs: {
      testnet: path.join(root, "nodes", "testnet"),
      mainnet: path.join(root, "nodes", "mainnet"),
    },
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
      const parsed = JSON.parse(fs.readFileSync(this.settingsPath, "utf-8")) as Partial<AppSettings>;
      return {
        ...fallback,
        ...parsed,
        dataDirs: {
          ...fallback.dataDirs,
          ...(parsed.dataDirs || {}),
        },
        selectedNetwork: parsed.selectedNetwork === "mainnet" ? "mainnet" : "testnet",
      };
    } catch {
      return fallback;
    }
  }

  update(patch: Partial<AppSettings>): AppSettings {
    const next = {
      ...this.get(),
      ...patch,
      dataDirs: {
        ...this.get().dataDirs,
        ...(patch.dataDirs || {}),
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

