import path from "node:path";
import { app } from "electron";
import type { NetworkConfig, NetworkId } from "../../../shared/types";

function defaultDataDir(network: NetworkId): string {
  return path.join(app.getPath("userData"), "nodes", network);
}

export function defaultNodePath(): string {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "bin", "picoin-node.exe");
  }
  return path.join(app.getAppPath(), "resources", "bin", "picoin-node.exe");
}

export function networkConfig(network: NetworkId, dataDir?: string): NetworkConfig {
  const resolvedDataDir = dataDir || defaultDataDir(network);
  if (network === "mainnet") {
    return {
      id: "mainnet",
      rpcUrl: "http://127.0.0.1:8000",
      chainName: "Picoin Mainnet",
      symbol: "PI",
      dataDir: resolvedDataDir,
      nodeArgs: [
        "--network",
        "mainnet",
        "--chain-id",
        "picoin-mainnet-v1",
        "--data-dir",
        resolvedDataDir,
        "--rpc",
        "127.0.0.1:8000",
      ],
    };
  }
  return {
    id: "testnet",
    rpcUrl: "http://127.0.0.1:18000",
    chainName: "Picoin Public Testnet",
    symbol: "PI",
    dataDir: resolvedDataDir,
    nodeArgs: [
      "--network",
      "public-testnet",
      "--chain-id",
      "picoin-public-testnet-v018",
      "--data-dir",
      resolvedDataDir,
      "--rpc",
      "127.0.0.1:18000",
    ],
  };
}

export function allNetworks(dataDirs?: Partial<Record<NetworkId, string>>): Record<NetworkId, NetworkConfig> {
  return {
    testnet: networkConfig("testnet", dataDirs?.testnet),
    mainnet: networkConfig("mainnet", dataDirs?.mainnet),
  };
}

