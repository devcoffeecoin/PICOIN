# Picoin Desktop Wallet V1

Electron + React + Vite + TypeScript desktop wallet for Picoin.

## Scope

V1 includes only:

- local node process management
- testnet/mainnet selector
- local RPC adapters
- create/import/export/lock/unlock wallet
- BIP39 seed phrase support
- encrypted local keystore
- send/receive PI
- dashboard and basic transaction history

No staking, mining dashboard, swaps, governance UI, or extra modules are included.

## Project Structure

```text
picoin-desktop-wallet/
  electron/
    main/
      config/networks.ts
      services/
        NodeManager.ts
        PicoinRPC.ts
        SettingsStore.ts
        WalletService.ts
        wallet/
          AddressEncoder.ts
          KeyProvider.ts
          Keystore.ts
          TransactionSigner.ts
          encoding.ts
      main.ts
    preload/preload.ts
  resources/bin/
    picoin-node.exe
  shared/types.ts
  src/
    App.tsx
    main.tsx
    styles.css
    types/picoin-api.d.ts
```

## Node Binary

Place the local Picoin node binary at:

```text
resources/bin/picoin-node.exe
```

For a packaged Windows build, `electron-builder` copies `resources/bin` into
the application resources. The app expects the final packaged path:

```text
resources/bin/picoin-node.exe
```

During development, Settings lets you override the node path.

## Network Profiles

The wallet has two networks:

- `testnet`
  - RPC: `http://127.0.0.1:18000`
  - chain: `Picoin Public Testnet`
  - symbol: `PI`
- `mainnet`
  - RPC: `http://127.0.0.1:8000`
  - chain: `Picoin Mainnet`
  - symbol: `PI`

Network selection and local paths are saved under Electron `userData`.

## Security Model

- Renderer has `nodeIntegration: false`.
- Renderer talks to main process only through `contextBridge`.
- Private keys are encrypted at rest with AES-256-GCM.
- KDF is PBKDF2-SHA256 with 310,000 iterations.
- Private keys are kept in memory only after unlock.
- Seed phrase is shown only once after wallet creation.
- The renderer never reads the keystore file directly.

## Wallet Crypto

V1 uses:

- BIP39 for seed phrase generation.
- Ed25519 keys encoded as `ed25519:<base64url>`.
- Picoin address encoding compatible with the current core wallet format.

TODO: confirm final mainnet wallet derivation path before public release. The
current `KeyProvider` is isolated so it can be swapped without changing the UI.

## RPC Adapters

Implemented adapter methods:

- `getBlockHeight()`
- `getSyncStatus()`
- `getPeers()`
- `getBalance(address)`
- `getTransactionHistory(address)`
- `broadcastTransaction(rawTx)`
- `sendTransaction(rawTx)`

Current endpoint mapping:

- `/node/sync-status`
- `/node/peers`
- `/accounts/:address`
- `/accounts/:address/history?limit=50`
- `/wallet/:address/nonce`
- `/transactions/submit`

TODO: update `PicoinRPC.ts` if final desktop node endpoint names change.

## Commands

Install dependencies:

```bash
npm install
```

Run Vite only:

```bash
npm run dev
```

Run Electron in development:

```bash
npm run electron:dev
```

Build frontend and Electron main/preload:

```bash
npm run build
```

Create Windows installer:

```bash
npm run dist
```

The installer output is written to:

```text
release/
```

V1 builds an unsigned installer. Code signing can be enabled later by removing
`signAndEditExecutable: false` and configuring a Windows signing certificate in
`electron-builder`.

## Mainnet Note

Mainnet should only be enabled for real funds after the final node binary,
mainnet genesis hash, and Picoin wallet derivation rules are frozen.
