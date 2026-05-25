import type { NetworkConfig, NetworkId } from "../../../shared/types";

export const DEFAULT_API_URLS: Record<NetworkId, string> = {
  testnet: "https://api.picoin.science",
  mainnet: "https://mainnet-api.picoin.science",
};

export function networkConfig(network: NetworkId, apiUrl?: string): NetworkConfig {
  if (network === "mainnet") {
    return {
      id: "mainnet",
      apiUrl: normalizeApiUrl(apiUrl || DEFAULT_API_URLS.mainnet),
      chainName: "Picoin Mainnet",
      symbol: "PI",
      networkId: "picoin-mainnet-v1",
      chainId: 314159,
    };
  }

  return {
    id: "testnet",
    apiUrl: normalizeApiUrl(apiUrl || DEFAULT_API_URLS.testnet),
    chainName: "Picoin Public Testnet",
    symbol: "PI",
    networkId: "public-testnet",
    chainId: "picoin-public-testnet-v018",
  };
}

export function allNetworks(apiUrls?: Partial<Record<NetworkId, string>>): Record<NetworkId, NetworkConfig> {
  return {
    testnet: networkConfig("testnet", apiUrls?.testnet),
    mainnet: networkConfig("mainnet", apiUrls?.mainnet),
  };
}

export function normalizeApiUrl(apiUrl: string): string {
  return apiUrl.trim().replace(/\/$/, "");
}
