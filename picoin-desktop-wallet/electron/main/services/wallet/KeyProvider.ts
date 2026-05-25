import crypto from "node:crypto";
import * as bip39 from "bip39";
import nacl from "tweetnacl";
import { decodeEd25519Key, encodeEd25519Key } from "./encoding";

export interface KeyMaterial {
  privateKey: string;
  publicKey: string;
  seedPhrase?: string;
}

export interface KeyProvider {
  createWallet(): KeyMaterial;
  importFromSeed(seedPhrase: string): KeyMaterial;
  importFromPrivateKey(privateKey: string): KeyMaterial;
}

export class Bip39Ed25519KeyProvider implements KeyProvider {
  createWallet(): KeyMaterial {
    const seedPhrase = bip39.generateMnemonic(256);
    return this.importFromSeed(seedPhrase);
  }

  importFromSeed(seedPhrase: string): KeyMaterial {
    const normalized = seedPhrase.trim().toLowerCase().replace(/\s+/g, " ");
    if (!bip39.validateMnemonic(normalized)) {
      throw new Error("invalid BIP39 seed phrase");
    }
    const seed = bip39.mnemonicToSeedSync(normalized);
    const privateSeed = crypto.createHash("sha256").update("picoin-wallet-v1:ed25519").update(seed).digest();
    const keypair = nacl.sign.keyPair.fromSeed(privateSeed);
    return {
      privateKey: encodeEd25519Key(privateSeed),
      publicKey: encodeEd25519Key(keypair.publicKey),
      seedPhrase: normalized,
    };
  }

  importFromPrivateKey(privateKey: string): KeyMaterial {
    const privateSeed = decodeEd25519Key(privateKey);
    const keypair = nacl.sign.keyPair.fromSeed(privateSeed);
    return {
      privateKey: encodeEd25519Key(privateSeed),
      publicKey: encodeEd25519Key(keypair.publicKey),
    };
  }
}

