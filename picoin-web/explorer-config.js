window.PICOIN_EXPLORER_CONFIG = {
  apiBaseUrl: "/api/bootstrap",
  refreshMs: 60000,
  nodes: [
    { label: "mainnet-primary", url: "/api/bootstrap" },
    { label: "pool1-full-node", url: "/api/pool1-node", enabled: false }
  ]
};
