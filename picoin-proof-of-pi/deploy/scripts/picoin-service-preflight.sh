#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${PICOIN_ENV_FILE:-/etc/picoin/picoin.env}"
FIX=0
REPO_DIR="${PICOIN_REPO_DIR:-${PICOIN_HOME:-/opt/picoin/picoin-proof-of-pi}}"

usage() {
  cat <<'EOF'
Usage:
  deploy/scripts/picoin-service-preflight.sh [--fix] [--repo-dir /opt/picoin/picoin-proof-of-pi]

Checks the writable directories declared by the Picoin systemd services. With
--fix, missing directories are created with the picoin user/group.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --fix)
      FIX=1
      shift
      ;;
    --repo-dir)
      REPO_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  REPO_DIR="${PICOIN_HOME:-$REPO_DIR}"
fi

PATHS=(
  "$REPO_DIR/data"
  "$REPO_DIR/test-output"
  "$REPO_DIR/backups"
  "/var/lib/picoin"
  "/var/backups/picoin"
  "/opt/picoin/state-backups"
)

missing=()
broken=()
for path in "${PATHS[@]}"; do
  if [ -L "$path" ] && [ ! -e "$path" ]; then
    broken+=("$path")
  elif [ ! -e "$path" ]; then
    missing+=("$path")
  elif [ ! -d "$path" ]; then
    echo "not a directory: $path" >&2
    exit 1
  fi
done

if [ "$FIX" -eq 1 ]; then
  if ! id picoin >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /opt/picoin --shell /bin/bash picoin
  fi
  for path in "${missing[@]}"; do
    install -d -o picoin -g picoin -m 0755 "$path"
  done
  for path in "${PATHS[@]}"; do
    if [ -e "$path" ]; then
      chown -h picoin:picoin "$path" 2>/dev/null || true
    fi
  done
fi

if [ "${#broken[@]}" -gt 0 ]; then
  printf 'broken systemd writable path: %s\n' "${broken[@]}" >&2
  exit 1
fi

if [ "${#missing[@]}" -gt 0 ] && [ "$FIX" -eq 0 ]; then
  printf 'missing systemd writable path: %s\n' "${missing[@]}" >&2
  echo "run with --fix as root to create them" >&2
  exit 1
fi

echo "Picoin service preflight ok"
