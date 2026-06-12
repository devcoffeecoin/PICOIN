# Picoin Desktop Wallet V1

Electron + React + Vite + TypeScript desktop wallet for Picoin.

## Scope

V1 is an API-connected wallet. It does not run or bundle a Picoin node.

Included:

- create/import/export/lock/unlock wallet
- BIP39 seed phrase support
- encrypted local keystore
- import from plain web wallet JSON or encrypted keystore JSON
- local transaction signing
- configurable mainnet/testnet API URLs
- balance lookup via API
- transaction history via API
- signed transaction broadcast via API
- send and receive PI

Not included:

- embedded node
- mining dashboard
- staking UI
- swaps
- governance UI

## Project Structure

```text
picoin-desktop-wallet/
  electron/
    main/
      config/networks.ts
      services/
        PicoinAPI.ts
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
  resources/icons/
    picoin-logo.ico
    picoin-logo.png
  shared/types.ts
  src/
    assets/picoin-logo.png
    App.tsx
    main.tsx
    styles.css
    types/picoin-api.d.ts
```

## Network Profiles

The wallet has two network profiles. API URLs are editable from Settings and
are stored under Electron `userData`.

- `mainnet`
  - default API: `https://api.picoin.science`
  - `network`: `picoin-mainnet-v1`
  - `chain_id`: `314159`
  - symbol: `PI`
- `testnet`
  - default API: `https://testnet-api.picoin.science`
  - `network`: `public-testnet`
  - `chain_id`: `picoin-public-testnet-v018`
  - symbol: `PI`

Mainnet uses the canonical API endpoint `https://api.picoin.science`.

## Security Model

- Renderer has `nodeIntegration: false`.
- Renderer talks to main process only through `contextBridge`.
- Private keys are encrypted at rest with AES-256-GCM.
- KDF is PBKDF2-SHA256 with 310,000 iterations.
- Private keys are kept in memory only after unlock.
- Seed phrase is shown only once after wallet creation.
- The renderer never reads the keystore file directly.
- Transactions are signed in the Electron main process before API broadcast.

## Wallet Crypto

V1 uses:

- BIP39 for seed phrase generation.
- Ed25519 keys encoded as `ed25519:<base64url>`.
- Picoin address encoding compatible with the current core wallet format.

TODO: confirm final mainnet wallet derivation path before public release. The
current `KeyProvider` is isolated so it can be swapped without changing the UI.

## Import Formats

The Wallet screen accepts these import formats:

- BIP39 seed phrase.
- Raw Ed25519 private key encoded as `ed25519:<base64url>`.
- Plain web wallet JSON, including wallets created locally on testnet. Supported
  fields include `address`, `public_key` or `publicKey`, `private_key` or
  `privateKey`, `seedPhrase` or `seed_phrase`, `network_id`, and `chain_id`.
- Encrypted Picoin keystore JSON using AES-256-GCM and PBKDF2-SHA256.

When importing JSON, the desktop wallet verifies that the supplied address and
public key match the private key or seed before saving the local encrypted
keystore.

## API Adapters

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
- `/protocol` fallback for API availability
- `/wallet/balance/:address`
- `/accounts/:address` fallback for balance
- `/transactions/:address`
- `/accounts/:address/history?limit=50` fallback for history
- `/transactions/recent?limit=50` fallback for history
- `/wallet/:address/nonce`
- `/transactions/submit`

Read requests use longer timeouts and the renderer avoids overlapping refreshes,
so slow public nodes should not cause repeated abort errors in the wallet UI.

The wallet profile names the selected profile field as `network`. Signed
transactions still use `network_id` in the payload because the current Picoin API
schema expects that field.

TODO: update `PicoinAPI.ts` if final public API endpoint names change.

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

Mainnet is the default profile for the public release. Keep the API URL,
mainnet genesis hash, and Picoin wallet derivation rules aligned with the
running protocol before distributing a production build.
