# Picoin Web

Static informational website for Picoin.

The home page is informational. Live protocol statistics belong in `explorer.html`.

## Links To Update Before Launch

Current public links:

- Explorer page: `explorer.html`
- Miner lookup page: `miner.html`
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

`apiBaseUrl` is the primary read API for blocks, mining metrics, miners, validators, events, mempool and lookup. `nodes` powers the public network comparison table and should list the public API URL for every mainnet node you want visitors to see.

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

The proxies forward read-only explorer traffic to the mainnet API. Override the default targets with `PICOIN_BOOTSTRAP_API_URL` and `PICOIN_VALIDATOR_API_URL` in Vercel if the canonical API endpoint changes.

If the website is served over HTTPS, the Picoin APIs must also be exposed over HTTPS; browsers block HTTP API calls from HTTPS pages.

## Local Preview

```bash
cd picoin-web
python -m http.server 8080
```

Open:

```text
http://127.0.0.1:8080
http://127.0.0.1:8080/explorer.html
http://127.0.0.1:8080/miner.html
http://127.0.0.1:8080/wallet.html
```

## Hosting

Deploy this folder to Netlify, Vercel, Cloudflare Pages, GitHub Pages, or any static hosting provider.

## Web Wallet

The public wallet route is served by the frontend deployment:

```text
https://www.picoin.science/wallet
```

It is not served by `api.picoin.science`. The wallet generates Ed25519 keys in the browser, stores the wallet JSON in `localStorage`, signs transactions locally, and talks to:

```text
https://api.picoin.science
```

The Vercel rewrite in `vercel.json` maps `/wallet` to `wallet.html`, `/explorer` to `explorer.html`, and `/miner` to `miner.html`.
