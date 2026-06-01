# Picoin Desktop Validator V1

Electron + React desktop validator for Picoin mainnet.

## Scope

- Runs a local Picoin node.
- Registers or loads a local validator identity.
- Validates jobs through the configured Picoin API.
- Shows node sync, replay, validator eligibility, quorum and logs.
- Submits wallet-backed validator stake from a selected Picoin wallet JSON.

The renderer never receives private keys. Validator identity and wallet signing stay in the Electron main process / Python backend.

## Mainnet Defaults

- API: `https://api.picoin.science`
- network_id: `picoin-mainnet-v1`
- chain_id: `314159`
- genesis_hash: `da286143167d14044c053fbb23fcf4673bb11bcd34fb1a11e5510ee8f8edb6e7`
- minimum validator stake: `31.416 PI`

## Development

```bash
cd picoin-desktop-validator
npm install
npm run electron:dev
```

The app uses `backend/` as the bundled Picoin runtime. In development, the local node database and identity are stored under Electron `userData`, not inside the repository.

## Build

```bash
cd picoin-desktop-validator
npm run build
npm run dist
```

Windows installers are written to `release/`.

## Backend Runtime

The app packages:

- `backend/app`
- `backend/picoin`
- `backend/validator`
- `backend/miner`
- `backend/requirements.txt`

Electron installs Python requirements using the detected Python 3.10+ runtime before starting node or validator actions.

## Notes

- The validator desktop intentionally embeds a local node. A validator should not vote from API data alone.
- The default local RPC port is `8131` to avoid colliding with server-side Picoin nodes on `8000`.
- Stake submission uses the selected wallet JSON through the Picoin CLI and does not expose the wallet private key to React.
