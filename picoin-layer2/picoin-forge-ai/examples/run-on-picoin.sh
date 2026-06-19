#!/usr/bin/env bash
set -euo pipefail

REPO_PATH="${1:-../../}"

picoin-forge scan "$REPO_PATH"
picoin-forge explain "$REPO_PATH"
picoin-forge install "$REPO_PATH"
picoin-forge service "$REPO_PATH"
picoin-forge health "$REPO_PATH"
picoin-forge l2-job create --repo-path "$REPO_PATH" --repo-url https://github.com/devcoffeecoin/PICOIN
picoin-forge l2-job verify --repo-path "$REPO_PATH"
