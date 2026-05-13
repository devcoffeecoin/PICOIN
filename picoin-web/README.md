# Picoin Web

Static informational website for Picoin.

This site does not fetch live network statistics. Protocol stats belong in the explorer/dashboard.

## Links To Update Before Launch

In `index.html`, update the explorer URL when the public explorer domain is ready:

```html
https://explorer.picoin.science
```

Current public links:

- Docs: https://solvency.gitbook.io/picoin
- X: https://x.com/Picoin_science
- GitHub: https://github.com/devcoffeecoin/PICOIN

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
