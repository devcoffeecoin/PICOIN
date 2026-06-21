#!/usr/bin/env bash
set -euo pipefail

APP_USER="${PICOIN_MARKETPLACE_USER:-picoin-marketplace}"
APP_DIR="${PICOIN_MARKETPLACE_APP_DIR:-/opt/picoin-marketplace}"
STATE_DIR="${PICOIN_MARKETPLACE_STATE_DIR:-/var/lib/picoin-marketplace}"
ENV_DIR="${PICOIN_MARKETPLACE_ENV_DIR:-/etc/picoin-marketplace}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --home "${STATE_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi

mkdir -p "${APP_DIR}" "${STATE_DIR}" "${ENV_DIR}"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude ".pytest_cache" \
    --exclude ".pytest_tmp*" \
    --exclude ".picoin-marketplace-state" \
    "${REPO_DIR}/" "${APP_DIR}/"
else
  cp -a "${REPO_DIR}/." "${APP_DIR}/"
fi

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -e "${APP_DIR}"

if [[ ! -f "${ENV_DIR}/picoin-marketplace.env" ]]; then
  cp "${APP_DIR}/deploy/picoin-marketplace.env.example" "${ENV_DIR}/picoin-marketplace.env"
fi

cp "${APP_DIR}/deploy/picoin-marketplace.service" /etc/systemd/system/picoin-marketplace.service
cp "${APP_DIR}/deploy/picoin-marketplace-scanner.service" /etc/systemd/system/picoin-marketplace-scanner.service
cp "${APP_DIR}/deploy/picoin-marketplace-maintenance.service" /etc/systemd/system/picoin-marketplace-maintenance.service

chown -R "${APP_USER}:${APP_USER}" "${STATE_DIR}" "${APP_DIR}" "${ENV_DIR}"
chmod 640 "${ENV_DIR}/picoin-marketplace.env"

systemctl daemon-reload
systemctl enable picoin-marketplace.service picoin-marketplace-scanner.service picoin-marketplace-maintenance.service
systemctl restart picoin-marketplace.service picoin-marketplace-scanner.service picoin-marketplace-maintenance.service

echo "Picoin Marketplace installed."
echo "Edit ${ENV_DIR}/picoin-marketplace.env for production addresses and RPC URLs."
echo "API health: curl -fsS http://127.0.0.1:9410/health"
