# Picoin Marketplace Deployment

This directory contains Linux deployment artifacts for:

- `picoin-marketplace`: FastAPI app on `127.0.0.1:9410`
- `picoin-marketplace-scanner`: deposit scanner loop for Picoin and EVM rails
- `picoin-marketplace-maintenance`: worker expiry and marketplace housekeeping
- `picoin-marketplace-miner`: optional real paired miner controller; for
  example Picoin plus XMRig reporting into the `PICOIN/MONERO` pool

## Install

From the repository root on the server:

```bash
cd picoin-marketplace
sudo bash deploy/install-marketplace.sh
```

Then edit:

```text
/etc/picoin-marketplace/picoin-marketplace.env
```

At minimum, set real values for:

```text
PICOIN_MARKETPLACE_ESCROW_ADDRESS
PICOIN_MARKETPLACE_PICOIN_NODE_URL
PICOIN_MARKETPLACE_EVM_ESCROW_ADDRESS
PICOIN_MARKETPLACE_EVM_RPC_URL
PICOIN_MARKETPLACE_SESSION_SECRET
```

For `https://marketplace.picoin.science`, also set:

```text
PICOIN_MARKETPLACE_SECURE_COOKIES=1
```

`PICOIN_MARKETPLACE_SESSION_SECRET` must be a long random value. New installs
generate one automatically; older deployments should add it manually before
exposing account dashboards.

Restart after editing:

```bash
sudo systemctl restart picoin-marketplace picoin-marketplace-scanner
sudo systemctl restart picoin-marketplace-maintenance
```

The real miner service is installed but not enabled automatically. Configure at
least `PICOIN_MARKETPLACE_PAIRED_MINER_COMMAND` first. For a true pair worker,
also configure `PICOIN_MARKETPLACE_PICOIN_MINER_COMMAND` and set
`PICOIN_MARKETPLACE_REQUIRE_PICOIN_MINER=1`, then run:

```bash
sudo systemctl enable --now picoin-marketplace-miner
```

## Status

```bash
systemctl status picoin-marketplace --no-pager -l
systemctl status picoin-marketplace-scanner --no-pager -l
systemctl status picoin-marketplace-maintenance --no-pager -l
systemctl status picoin-marketplace-miner --no-pager -l
journalctl -u picoin-marketplace -f
journalctl -u picoin-marketplace-scanner -f
journalctl -u picoin-marketplace-maintenance -f
journalctl -u picoin-marketplace-miner -f
```

## Nginx

The sample `nginx-marketplace.conf` proxies `marketplace.picoin.science` to
`127.0.0.1:9410`.

Example:

```bash
sudo cp deploy/nginx-marketplace.conf /etc/nginx/sites-available/marketplace.picoin.science
sudo ln -sf /etc/nginx/sites-available/marketplace.picoin.science /etc/nginx/sites-enabled/marketplace.picoin.science
sudo nginx -t
sudo systemctl reload nginx
```

Add TLS with certbot or the existing production proxy flow.
