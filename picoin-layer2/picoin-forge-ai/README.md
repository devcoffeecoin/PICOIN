# Picoin Forge AI

Picoin Forge AI is an MVP for a future Picoin Layer 2 compute agent.

The first version is a Python CLI named `picoin-forge`. It analyzes a repository, maps architecture, generates safe Linux install scripts, prepares systemd service drafts, creates health reports, reads logs, and simulates verifiable Layer 2 jobs.

This MVP does not integrate with Picoin Layer 1 payments or real blockchain settlement yet. It prepares the interfaces for future integration.

## Why This Exists

Picoin Layer 1 should stay focused on:

- Consensus.
- Payments in PI.
- Staking.
- Validator security.
- Worker identity.
- Reputation.
- Job result hashes.
- Validation records.

Picoin Forge AI runs useful work off-chain:

- Repository analysis.
- Linux setup planning.
- Service configuration.
- Health checking.
- Error diagnosis.
- Deployment preparation.

Workers execute jobs. Validators verify evidence. Layer 1 can later register the job hash, reputation updates, and payment settlement.

## Install

```bash
cd picoin-layer2/picoin-forge-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

## Commands

```bash
picoin-forge scan /path/to/repo
picoin-forge explain /path/to/repo
picoin-forge install /path/to/repo
picoin-forge service /path/to/repo
picoin-forge health /path/to/repo
picoin-forge fix /path/to/repo
picoin-forge l2-job create --repo-path /path/to/repo --repo-url https://github.com/devcoffeecoin/PICOIN
picoin-forge l2-job verify --repo-path /path/to/repo
```

## Generated Files

Inside the scanned repository:

```text
.picoin-forge/
  project-map.json
  ignored-sensitive-files.txt
  scripts/
    install.sh
    health_check.sh
  systemd/
    picoin-node.service
    picoin-api.service
  reports/
    architecture.md
    health-report.md
    fix-suggestions.md
  l2-jobs/
    job_*.json
```

## Safety Rules

- The MVP generates scripts; it does not run dangerous setup commands automatically.
- It never calls `sudo` inside generated install scripts.
- It ignores sensitive paths such as `.env`, wallets, keys, seeds, and identities.
- It does not overwrite existing generated files without creating a timestamped backup.
- `fix` only writes suggestions; it does not modify application code.

## Picoin Example

```bash
picoin-forge scan ../../
picoin-forge explain ../../
picoin-forge install ../../
picoin-forge service ../../
picoin-forge health ../../
picoin-forge l2-job create --repo-path ../../ --repo-url https://github.com/devcoffeecoin/PICOIN
picoin-forge l2-job verify --repo-path ../../
```
