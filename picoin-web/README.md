# Picoin Web

Static public website for Picoin Testnet Alpha.

## Configure API

Edit `config.js` before deployment:

```js
window.PICOIN_CONFIG = {
  apiBaseUrl: "https://your-testnet-api.example.com"
};
```

For a raw droplet IP during testnet:

```js
window.PICOIN_CONFIG = {
  apiBaseUrl: "http://YOUR_DROPLET_IP:8000"
};
```

## Local Preview

From this folder:

```bash
python -m http.server 8080
```

Open:

```text
http://127.0.0.1:8080
```

## Hosting

This folder is static and can be deployed to Netlify, Vercel, Cloudflare Pages, GitHub Pages, or any static hosting provider.

## Picoin API CORS

If this website is hosted on another domain, the Picoin node must allow that origin:

```bash
export PICOIN_CORS_ORIGINS="https://your-site.example.com,http://localhost:8080"
python -m picoin node start --host 0.0.0.0 --port 8000
```
