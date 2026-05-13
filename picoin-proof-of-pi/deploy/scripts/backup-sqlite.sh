#!/usr/bin/env bash
set -euo pipefail

PICOIN_HOME="${PICOIN_HOME:-/opt/picoin/picoin-proof-of-pi}"
DB_PATH="${PICOIN_DB_PATH:-$PICOIN_HOME/data/picoin.sqlite3}"
BACKUP_DIR="${PICOIN_BACKUP_DIR:-$PICOIN_HOME/backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/picoin-$STAMP.sqlite3"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_PATH" ]; then
  echo "database not found: $DB_PATH" >&2
  exit 1
fi

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" ".backup '$OUT'"
else
  cp "$DB_PATH" "$OUT"
fi

gzip -f "$OUT"
echo "$OUT.gz"
