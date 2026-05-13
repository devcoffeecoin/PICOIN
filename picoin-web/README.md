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
  apiBaseUrl: "https://your-public-picoin-api.example.com"
};
```

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
