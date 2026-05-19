# Picoin Web

Static informational website for Picoin.

The home page is informational. Live protocol statistics belong in `explorer.html`.

## Links To Update Before Launch

Current public links:

- Explorer page: `explorer.html`
- Docs: https://solvency.gitbook.io/picoin
- X: https://x.com/Picoin_science
- GitHub: https://github.com/devcoffeecoin/PICOIN

## Explorer API

Edit `explorer-config.js` before deployment:

```js
window.PICOIN_EXPLORER_CONFIG = {
  apiBaseUrl: "https://your-public-picoin-api.example.com",
  refreshMs: 30000,
  nodes: [
    { label: "bootstrap", url: "https://bootstrap-api.example.com" },
    { label: "validator", url: "https://validator-api.example.com" }
  ]
};
```

`apiBaseUrl` is the primary read API for blocks, validators, events, mempool and lookup. `nodes` powers the public network comparison table and should list the public API URL for every testnet node you want visitors to see.

The public Vercel deployment can use the included same-origin proxies:

```js
window.PICOIN_EXPLORER_CONFIG = {
  apiBaseUrl: "/api/bootstrap",
  refreshMs: 30000,
  nodes: [
    { label: "bootstrap", url: "/api/bootstrap" },
    { label: "validator", url: "/api/validator" }
  ]
};
```

The proxies forward read-only explorer traffic to the public testnet droplets. Override the default targets with `PICOIN_BOOTSTRAP_API_URL` and `PICOIN_VALIDATOR_API_URL` in Vercel if the droplet IPs change.

If the website is served over HTTPS, the Picoin APIs must also be exposed over HTTPS; browsers block HTTP API calls from HTTPS pages.

## Local Preview

```bash
cd picoin-web
python -m http.server 8080
```

Open:

```text
http://127.0.0.1:8080
```

## Hosting

Deploy this folder to Netlify, Vercel, Cloudflare Pages, GitHub Pages, or any static hosting provider.
