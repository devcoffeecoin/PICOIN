window.PICOIN_EXPLORER_CONFIG = {
  apiBaseUrl: "/api/bootstrap",
  refreshMs: 60000,
  nodes: [
    { label: "mainnet-primary", url: "/api/bootstrap" },
    { label: "candidate-a", url: "/api/bootstrap-a" },
    { label: "candidate-b", url: "/api/bootstrap-b" },
    { label: "candidate-c", url: "/api/bootstrap-c" }
  ]
};
