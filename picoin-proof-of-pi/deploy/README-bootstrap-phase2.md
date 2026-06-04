# Picoin Phase 2 Public Bootstrap Candidates

This runbook starts Phase 2 of the decentralization roadmap. The goal is to prove that multiple public bootstrap candidates can serve the same mainnet chain view before explorer, wallet, miner, or validator failover is enabled.

Do not run this on the current mainnet bootstrap. Use disposable candidate droplets first.

## Scope

Phase 2 candidates are read-only API/node servers:

- `picoin-node` enabled
- `picoin-miner` disabled
- `picoin-validator` disabled
- `picoin-reconciler` disabled
- `picoin-auditor` disabled

They may expose `http://PUBLIC_IP:8000` during the lab. Add Nginx/TLS only after the raw node checks pass.

## Current Candidate Endpoints

These endpoints are Phase 2 public bootstrap candidates. They are read-only candidates for decentralization testing, not yet the production failover list for explorer, wallets, miners, or validators.

| Candidate | Region label | Endpoint | Role |
| --- | --- | --- | --- |
| `mainnet-bootstrap-candidate-a` | lon1 | `http://178.62.30.17:8000` | read-only public bootstrap candidate |
| `mainnet-bootstrap-candidate-b` | lon1-02 | `http://138.68.139.141:8000` | read-only public bootstrap candidate |
| `mainnet-bootstrap-candidate-c` | tor1 | `http://159.89.115.183:8000` | read-only public bootstrap candidate |

Keep the current production API endpoint, `https://api.picoin.science`, as the canonical mainnet bootstrap until explorer, wallet, miner, and validator failover are explicitly enabled and tested.

## Operator Requirements

A Phase 2 bootstrap candidate operator must:

- run a clean Ubuntu 22.04 or 24.04 server with enough disk for the mainnet SQLite database, logs, and backups
- expose `picoin-node` publicly on the configured node address
- keep `picoin-miner`, `picoin-validator`, `picoin-reconciler`, and `picoin-auditor` disabled unless the node is later promoted under a separate production plan
- use the production mainnet identity values: `picoin-mainnet-v1`, chain id `314159`, protocol version `1.0`, and the canonical mainnet genesis hash
- preserve a unique `PICOIN_NODE_ID` and `PICOIN_NODE_ADDRESS` per candidate
- restore from a canonical peer or another already verified candidate, then verify zero replay divergence
- pass `bootstrap-phase2-verify.py` against the rest of the candidate set before being listed as healthy
- avoid manual SQLite edits, database copies from unknown nodes, or private wallet files on the bootstrap candidate
- monitor `/health`, `/node/sync-status`, `/audit/full`, `/validators/status`, `/miners/status`, `/stats`, `/blocks`, and `/mempool/status`
- be disposable: if the candidate diverges or fails verification, rebuild it from a verified snapshot instead of repairing it by hand

## Candidate Install

Run on each clean Ubuntu 22.04/24.04 candidate droplet:

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv python3-pip sqlite3 curl jq rsync

sudo useradd --system --create-home --home-dir /var/lib/picoin --shell /usr/sbin/nologin picoin 2>/dev/null || true
sudo mkdir -p /opt/picoin/src /var/lib/picoin/data-mainnet-bootstrap-candidate /etc/picoin /var/backups/picoin-mainnet-bootstrap-candidate
sudo chown -R picoin:picoin /var/lib/picoin /var/backups/picoin-mainnet-bootstrap-candidate

cd /opt/picoin/src
sudo git clone https://github.com/devcoffeecoin/PICOIN.git PICOIN 2>/dev/null || true
cd /opt/picoin/src/PICOIN
sudo git fetch origin
sudo git switch codex/decentralization-roadmap-phase1
sudo git pull --ff-only origin codex/decentralization-roadmap-phase1
```

Install the runtime copy:

```bash
SOURCE_DIR=/opt/picoin/src/PICOIN/picoin-proof-of-pi

sudo env \
  PICOIN_SOURCE_DIR="$SOURCE_DIR" \
  PICOIN_REPO_DIR=/opt/picoin/picoin-proof-of-pi \
  PICOIN_DATA_DIR=/var/lib/picoin/data-mainnet-bootstrap-candidate \
  bash "$SOURCE_DIR/deploy/scripts/refresh-code.sh"

cd /opt/picoin/picoin-proof-of-pi
sudo -u picoin python3 -m venv .venv
sudo -u picoin .venv/bin/pip install --upgrade pip
sudo -u picoin .venv/bin/pip install -r requirements.txt
```

## Environment

```bash
sudo cp /opt/picoin/picoin-proof-of-pi/deploy/mainnet-public-bootstrap-candidate.env.example /etc/picoin/picoin.env
sudo sed -i 's/CHANGE_ME_BOOTSTRAP_CANDIDATE_NODE_ID/mainnet-bootstrap-candidate-REGION-N/' /etc/picoin/picoin.env
sudo sed -i 's/CHANGE_ME_PUBLIC_IP/YOUR_DROPLET_PUBLIC_IP/' /etc/picoin/picoin.env
sudo chown root:picoin /etc/picoin/picoin.env
sudo chmod 0640 /etc/picoin/picoin.env
sudo grep -n "CHANGE_ME\|YOUR_DROPLET_PUBLIC_IP" /etc/picoin/picoin.env
```

The final grep must print nothing before services start.

## Start Candidate

```bash
sudo systemctl daemon-reload
sudo systemctl disable --now picoin-miner picoin-validator picoin-reconciler picoin-auditor 2>/dev/null || true
sudo systemctl enable --now picoin-node
sleep 20

systemctl is-active picoin-node picoin-miner picoin-validator picoin-reconciler picoin-auditor 2>/dev/null || true
curl -sS http://127.0.0.1:8000/health | python3 -m json.tool
curl -sS http://127.0.0.1:8000/node/sync-status | python3 -m json.tool
```

For a read-only candidate, `/health` may be `degraded` only because there are not enough eligible validators on that local node. Replay must still be healthy and not divergent.

## Restore Mainnet Snapshot

```bash
cd /opt/picoin/picoin-proof-of-pi
set -a
. /etc/picoin/picoin.env
set +a

HEAD_HEIGHT=$(curl -sS --max-time 30 "$PICOIN_BOOTSTRAP_PEER/node/sync-status" | \
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(int(d.get("effective_latest_block_height") or d.get("latest_block_height") or 1))')
echo "HEAD_HEIGHT=$HEAD_HEIGHT"

sudo -u picoin .venv/bin/python -m picoin node checkpoint \
  --server http://127.0.0.1:8000 \
  restore-peer \
  --peer "$PICOIN_BOOTSTRAP_PEER" \
  --height "$HEAD_HEIGHT" \
  --source phase2-bootstrap-candidate-restore
```

Then verify the candidate against the current mainnet bootstrap:

```bash
sudo -u picoin PICOIN_ENV_FILE=/etc/picoin/picoin.env \
  bash deploy/scripts/full-node-phase1-verify.sh
```

Expected final result:

```json
{
  "status": "ok",
  "summary": {
    "errors": 0
  }
}
```

If it is briefly behind, run catch-up and verify again:

```bash
sudo -u picoin .venv/bin/python -m picoin node catch-up \
  --server http://127.0.0.1:8000 \
  --peer "$PICOIN_BOOTSTRAP_PEER" \
  --max-rounds 10 \
  --reconcile-limit 20 \
  --replay-limit 20
```

Final acceptance requires zero lag, matching effective tip hash, audit valid, and no replay divergence.

## Compare Public Candidates

Run from any machine that can reach all candidates:

```bash
cd /opt/picoin/picoin-proof-of-pi

python3 deploy/scripts/bootstrap-phase2-verify.py \
  https://api.picoin.science \
  http://CANDIDATE_A_PUBLIC_IP:8000 \
  http://CANDIDATE_B_PUBLIC_IP:8000 \
  http://CANDIDATE_C_PUBLIC_IP:8000
```

Expected result:

```json
{
  "status": "ok",
  "summary": {
    "errors": 0
  }
}
```

The verifier checks:

- required number of bootstrap endpoints are reachable
- health is `ok`, or `degraded` only for the expected read-only quorum issue
- replay is healthy and not divergent
- `/audit/full` is valid
- network id, chain id, genesis hash, and protocol version match
- effective height lag is within the configured limit
- effective tip hash matches when all candidates are at the same height
- checkpoint or active snapshot hashes match when comparable
- read-only API endpoints respond for stats, blocks, mempool status, validators, and miners

Use strict checkpoint mode for final acceptance:

```bash
PICOIN_PHASE2_STRICT_CHECKPOINT=1 \
python3 deploy/scripts/bootstrap-phase2-verify.py \
  https://api.picoin.science \
  http://CANDIDATE_A_PUBLIC_IP:8000 \
  http://CANDIDATE_B_PUBLIC_IP:8000 \
  http://CANDIDATE_C_PUBLIC_IP:8000
```

## Single-Candidate Failure Drill

This drill proves that the candidate set has more than one healthy read-only endpoint. Do not change production explorer, wallet, miner, or validator configuration during this drill.

1. Verify all candidates return `status=ok` from `bootstrap-phase2-verify.py`.
2. Stop one candidate:

```bash
sudo systemctl stop picoin-node
```

3. Re-run the verifier using the remaining healthy endpoints only.
4. Start the stopped candidate again and wait for catch-up:

```bash
sudo systemctl start picoin-node
sleep 30
```

5. Re-run full verification with all candidates.

The drill passes only if the remaining candidates continue to match mainnet while one candidate is offline, and the stopped candidate catches up without manual database edits.

## Explorer And Wallet Read Failover

The web frontend can read through the Phase 2 candidate set without exposing `http://` bootstrap URLs directly to HTTPS browsers. Vercel rewrites same-origin routes to each candidate:

| Web route | Upstream |
| --- | --- |
| `/api/bootstrap` | `https://api.picoin.science` |
| `/api/bootstrap-a` | `http://178.62.30.17:8000` |
| `/api/bootstrap-b` | `http://138.68.139.141:8000` |
| `/api/bootstrap-c` | `http://159.89.115.183:8000` |

The static web pages use `api-failover.js` to try the last healthy bootstrap first, then fall through to the rest of the configured endpoint list. The explorer, miner search, transaction page, and wallet balance/history reads use this path.

Signed wallet transaction submission remains pinned to the primary route. Do not enable write failover to read-only candidates until transaction propagation through multiple public bootstraps is explicitly tested.

After deploying the web frontend, verify:

```bash
for path in health protocol node/sync-status blocks validators/status miners/status stats; do
  curl -i --max-time 20 "https://picoin.science/api/bootstrap/$path" | head -20
  curl -i --max-time 20 "https://picoin.science/api/bootstrap-a/$path" | head -20
  curl -i --max-time 20 "https://picoin.science/api/bootstrap-b/$path" | head -20
  curl -i --max-time 20 "https://picoin.science/api/bootstrap-c/$path" | head -20
done
```

Then stop one candidate, refresh explorer and wallet, and confirm read-only data continues loading from the remaining endpoints. This closes the production failover gate only after the deployed site is observed working while one bootstrap is offline.

## Acceptance Gates

Phase 2 is not ready for production failover until all of these are true:

- at least three public bootstrap candidates are healthy in different regions
- all candidates match mainnet network id, chain id, genesis hash, protocol version, height, and effective tip hash
- `/audit/full` is valid on every candidate
- read-only explorer/wallet endpoints return JSON locally and publicly
- one candidate can be stopped without losing at least two healthy read-only candidates
- no miner, validator, reconciler, or auditor service is enabled on candidate droplets
- the endpoint list and operator requirements are documented before explorer/wallet failover is enabled

## Rollback

Candidates are disposable. If a candidate diverges or fails verification:

```bash
sudo systemctl stop picoin-node
sudo systemctl disable picoin-node
```

Do not copy candidate SQLite files into the mainnet bootstrap. Rebuild the candidate from snapshot restore instead.
