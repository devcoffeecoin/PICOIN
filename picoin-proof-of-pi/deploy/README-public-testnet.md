# Picoin Public Testnet Deployment Kit v0.19

This kit turns the current Picoin node into a repeatable public testnet deployment for Ubuntu/DigitalOcean.

It does not activate Science Compute L2, AI execution, bridges, smart contracts or mainnet economics. The Science Compute Reserve remains locked by default.

## Recommended Droplet

For the first bootstrap node:

- Ubuntu 24.04 LTS
- Basic Premium Intel or AMD
- 1 vCPU / 2 GB RAM / 70 GB disk is enough for early testnet
- Open inbound TCP `22` and `8000`
- Keep the public website on separate hosting

For longer-running public nodes, move to 2 vCPU / 4 GB RAM before inviting external miners.

## Install

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip sqlite3 curl ufw
sudo useradd --system --create-home --home-dir /opt/picoin --shell /bin/bash picoin
sudo -u picoin git clone https://github.com/devcoffeecoin/PICOIN.git /opt/picoin/PICOIN
sudo -u picoin bash -lc 'cd /opt/picoin/PICOIN/picoin-proof-of-pi && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'
sudo -u picoin bash -lc 'ln -s /opt/picoin/PICOIN/picoin-proof-of-pi /opt/picoin/picoin-proof-of-pi'
sudo PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi /opt/picoin/picoin-proof-of-pi/deploy/scripts/install-systemd-service.sh
```

Runtime state is intentionally separated from code:

- chain database and identities: `/var/lib/picoin/data`
- compressed SQLite backups: `/var/backups/picoin`
- pre-refresh data copies: `/opt/picoin/state-backups`

Edit the public testnet environment:

```bash
sudo nano /etc/picoin/picoin.env
```

For the first bootstrap node set:

```bash
PICOIN_NODE_ID=bootstrap-nyc1-1
PICOIN_NODE_TYPE=bootstrap
PICOIN_NODE_ADDRESS=http://YOUR_PUBLIC_IP:8000
PICOIN_BOOTSTRAP_PEERS=
PICOIN_BOOTSTRAP_PEER=
```

For a second node set:

```bash
PICOIN_NODE_ID=validator-nyc1-1
PICOIN_NODE_TYPE=validator
PICOIN_NODE_ADDRESS=http://SECOND_PUBLIC_IP:8000
PICOIN_BOOTSTRAP_PEERS=http://BOOTSTRAP_PUBLIC_IP:8000
PICOIN_BOOTSTRAP_PEER=http://BOOTSTRAP_PUBLIC_IP:8000
```

If the testnet needs initial wallet funding, use the same canonical allocation file on every node:

```bash
sudo cp /opt/picoin/picoin-proof-of-pi/deploy/public-testnet-genesis.allocations.example.json /etc/picoin/genesis.allocations.json
sudo chown root:picoin /etc/picoin/genesis.allocations.json
sudo chmod 640 /etc/picoin/genesis.allocations.json
/opt/picoin/picoin-proof-of-pi/.venv/bin/python -m picoin node genesis-hash --file /etc/picoin/genesis.allocations.json
```

Then set the same values on every node:

```bash
PICOIN_GENESIS_ALLOCATIONS_FILE=/etc/picoin/genesis.allocations.json
PICOIN_GENESIS_HASH=THE_HASH_PRINTED_ABOVE
```

Start:

```bash
sudo systemctl start picoin-node
sudo systemctl status picoin-node --no-pager
```

Optional worker services are installed too:

```bash
sudo systemctl start picoin-validator
sudo systemctl start picoin-miner
sudo systemctl start picoin-reconciler
sudo systemctl start picoin-auditor
sudo systemctl status picoin-validator picoin-miner picoin-reconciler picoin-auditor --no-pager
```

`picoin-miner` and `picoin-validator` run through `deploy/scripts/picoin-worker-loop.sh`. A single miner or validator iteration can exit non-zero when there is no block or validation job ready; the wrapper treats that as a normal polling cycle and keeps the systemd service active.

`picoin-reconciler` runs `node catch-up` in the background every `PICOIN_RECONCILER_SLEEP_SECONDS` seconds, writing JSON output to `PICOIN_RECONCILE_DIR`. It is the normal way for public nodes to converge after short peer outages or delayed gossip.

Use different identity files per droplet by editing `/etc/picoin/picoin.env`:

```bash
PICOIN_MINER_IDENTITY=/opt/picoin/picoin-proof-of-pi/data/testnet/identities/miner-alice.json
PICOIN_VALIDATOR_IDENTITY=/opt/picoin/picoin-proof-of-pi/data/testnet/identities/validator-one.json
```

If using the default persistent state directory from this kit, prefer:

```bash
PICOIN_MINER_IDENTITY=/var/lib/picoin/data/testnet/identities/miner-alice.json
PICOIN_VALIDATOR_IDENTITY=/var/lib/picoin/data/testnet/identities/validator-one.json
```

## Safe Code Refresh

Do not delete `/opt/picoin/picoin-proof-of-pi` by hand on a running public node. Use the refresh helper; it stops services, backs up existing state, migrates `data/` to `/var/lib/picoin/data`, refreshes code while excluding state, reinstalls systemd units, and leaves `/opt/picoin/picoin-proof-of-pi/data` as a symlink to the persistent state directory.

```bash
cd /opt/picoin/PICOIN/picoin-proof-of-pi
git pull
sudo PICOIN_SOURCE_DIR="$(pwd)" \
  PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
  /opt/picoin/picoin-proof-of-pi/deploy/scripts/refresh-code.sh
sudo systemctl restart picoin-node picoin-auditor picoin-reconciler picoin-validator picoin-miner
```

Then verify:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node report --peer http://BOOTSTRAP_PUBLIC_IP:8000
```

## Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp
sudo ufw enable
sudo ufw status
```

## Public Checks

On the node:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin node doctor
.venv/bin/python -m picoin node sync-status
.venv/bin/python -m picoin node audit
```

From another machine:

```bash
curl http://YOUR_PUBLIC_IP:8000/health
curl http://YOUR_PUBLIC_IP:8000/node/sync-status
```

The deployment script also installs a standalone health checker:

```bash
PICOIN_SERVER=http://127.0.0.1:8000 /opt/picoin/picoin-proof-of-pi/deploy/scripts/health-check.sh
```

Use the service check when promoting a node or after code refreshes. It verifies systemd units, persistent data paths, smoke/backups, sync, audit and peer report in one pass:

```bash
PICOIN_PUBLIC_API_URL=https://api.picoin.science /opt/picoin/picoin-proof-of-pi/deploy/scripts/public-testnet-service-check.sh
```

The check should end with `PICOIN_SERVICE_CHECK_STATUS=ok`.

For a full public-testnet smoke check:

```bash
PICOIN_SERVER=http://127.0.0.1:8000 \
PICOIN_BOOTSTRAP_PEER=http://BOOTSTRAP_PUBLIC_IP:8000 \
/opt/picoin/picoin-proof-of-pi/deploy/scripts/public-testnet-smoke.sh
```

On the bootstrap node, omit `PICOIN_BOOTSTRAP_PEER`. The smoke script runs `node catch-up`, `node report` and `node audit`, stores JSON output in `data/testnet/smoke`, prints `PICOIN_SMOKE_STATUS=ok` on success and exits non-zero on failure.

## Backups

Run a manual SQLite backup:

```bash
sudo -u picoin PICOIN_HOME=/opt/picoin/picoin-proof-of-pi /opt/picoin/picoin-proof-of-pi/deploy/scripts/backup-sqlite.sh
```

Suggested cron:

```bash
sudo crontab -u picoin -e
```

```cron
*/30 * * * * /opt/picoin/picoin-proof-of-pi/deploy/scripts/backup-sqlite.sh >/var/backups/picoin/latest.log 2>&1
```

## Bootstrap Flow

For a fresh one-node public testnet:

```bash
cd /opt/picoin/picoin-proof-of-pi
.venv/bin/python -m picoin testnet reset
sudo systemctl restart picoin-node
.venv/bin/python -m picoin testnet bootstrap
.venv/bin/python -m picoin testnet cycle
.venv/bin/python -m picoin node checkpoint create
.venv/bin/python -m picoin node doctor --require-checkpoint
```

For continuous mining:

```bash
.venv/bin/python -m picoin testnet continuous --miners 1 --loops 10 --workers 1 --faucet 0
.venv/bin/python -m picoin node audit
.venv/bin/python -m picoin node checkpoint latest
```

## Connect A Second Droplet

1. Install the same kit.
2. Set `PICOIN_BOOTSTRAP_PEERS=http://BOOTSTRAP_PUBLIC_IP:8000`.
3. Set a unique `PICOIN_NODE_ID`.
4. Start the node.
5. On each node run:

```bash
.venv/bin/python -m picoin node reconcile
.venv/bin/python -m picoin node catch-up --peer http://BOOTSTRAP_PUBLIC_IP:8000
.venv/bin/python -m picoin node compare --peer http://BOOTSTRAP_PUBLIC_IP:8000
.venv/bin/python -m picoin node report --peer http://BOOTSTRAP_PUBLIC_IP:8000
.venv/bin/python -m picoin node peers
.venv/bin/python -m picoin node sync-status
```

`node catch-up` runs reconcile, consensus replay, sync-status and audit in bounded rounds. With `--peer`, it also compares `network_id`, `chain_id`, `genesis_hash`, latest height and latest block hash against the peer. It should end with `status=ok`, `peer_matches=true`, `pending_replay_blocks=0` and `audit_valid=true`.

`node report` is read-only and returns a pass/fail readiness checklist for health, audit, replay backlog, consensus, Science Compute Reserve, treasury and peer state.

`node compare` is the shortest read-only peer check. It compares `network_id`, `chain_id`, `genesis_hash`, latest height and latest block hash.

Both nodes should eventually report compatible `network_id`, `chain_id`, `genesis_hash`, latest height and latest block hash.

After both nodes are connected, the short operational check is:

```bash
PICOIN_BOOTSTRAP_PEER=http://BOOTSTRAP_PUBLIC_IP:8000 deploy/scripts/public-testnet-smoke.sh
```

Suggested monitoring cron:

```cron
*/5 * * * * PICOIN_BOOTSTRAP_PEER=http://BOOTSTRAP_PUBLIC_IP:8000 /opt/picoin/picoin-proof-of-pi/deploy/scripts/public-testnet-smoke.sh >>/var/lib/picoin/data/testnet/smoke/cron.log 2>&1
```

## Operational Checklist

- `/health` returns `status=ok`.
- `node doctor` has no errors.
- `node audit` reports `valid=true`.
- `deploy/scripts/public-testnet-smoke.sh` exits with `PICOIN_SMOKE_STATUS=ok`.
- Latest block hash is stable across nodes.
- At least one checkpoint exists after mining.
- Backups are being created.
- Science Compute Reserve status remains locked until a future governance activation.
- Treasury claims remain protected by the 90-day timelock.
