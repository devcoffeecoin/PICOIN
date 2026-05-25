import fs from "node:fs";
import path from "node:path";
import { app } from "electron";
import type {
  CreateWalletResult,
  KeystoreExport,
  NetworkConfig,
  SignedTransaction,
  TransactionDraft,
  WalletSummary,
} from "../../../shared/types";
import { PicoinAddressEncoder } from "./wallet/AddressEncoder";
import { encryptKeystore, decryptKeystore, type EncryptedKeystore, type PlainSecrets } from "./wallet/Keystore";
import { Bip39Ed25519KeyProvider, type KeyMaterial } from "./wallet/KeyProvider";
import { Ed25519TransactionSigner } from "./wallet/TransactionSigner";
import { canonicalAmount, toUnits } from "./wallet/encoding";

export class WalletService {
  private readonly keyProvider = new Bip39Ed25519KeyProvider();
  private readonly addressEncoder = new PicoinAddressEncoder();
  private readonly signer = new Ed25519TransactionSigner();
  private readonly keystorePath: string;
  private secrets: PlainSecrets | null = null;

  constructor() {
    this.keystorePath = path.join(app.getPath("userData"), "wallet-keystore.json");
  }

  getSummary(): WalletSummary {
    const keystore = this.readKeystore();
    return {
      hasWallet: Boolean(keystore),
      locked: !this.secrets,
      address: keystore?.address ?? null,
      publicKey: keystore?.publicKey ?? null,
      networkId: keystore?.networkId,
      chainId: keystore?.chainId,
    };
  }

  createWallet(password: string, network: NetworkConfig): CreateWalletResult {
    const material = this.keyProvider.createWallet();
    const keystore = this.saveMaterial(material, password, network);
    this.secrets = { privateKey: material.privateKey, seedPhrase: material.seedPhrase };
    return {
      ...this.summaryFromKeystore(keystore),
      locked: false,
      seedPhrase: material.seedPhrase || "",
    };
  }

  importFromSeed(seedPhrase: string, password: string, network: NetworkConfig): WalletSummary {
    const material = this.keyProvider.importFromSeed(seedPhrase);
    const keystore = this.saveMaterial(material, password, network);
    this.secrets = { privateKey: material.privateKey, seedPhrase: material.seedPhrase };
    return { ...this.summaryFromKeystore(keystore), locked: false };
  }

  importFromPrivateKey(privateKey: string, password: string, network: NetworkConfig): WalletSummary {
    const material = this.keyProvider.importFromPrivateKey(privateKey);
    const keystore = this.saveMaterial(material, password, network);
    this.secrets = { privateKey: material.privateKey };
    return { ...this.summaryFromKeystore(keystore), locked: false };
  }

  exportKeystore(password: string): KeystoreExport {
    const keystore = this.requireKeystore();
    decryptKeystore(keystore, password);
    return {
      fileName: `picoin-keystore-${keystore.address}.json`,
      keystore: JSON.stringify(keystore, null, 2),
    };
  }

  unlockWallet(password: string): WalletSummary {
    const keystore = this.requireKeystore();
    this.secrets = decryptKeystore(keystore, password);
    return { ...this.summaryFromKeystore(keystore), locked: false };
  }

  lockWallet(): WalletSummary {
    this.secrets = null;
    return this.getSummary();
  }

  getAddress(): string | null {
    return this.getSummary().address;
  }

  signTransaction(
    tx: {
      to: string;
      amount: number;
      fee: number;
      nonce: number;
      payload?: Record<string, unknown>;
      txType?: string;
    },
    network: NetworkConfig,
  ): SignedTransaction {
    const secrets = this.requireUnlockedSecrets();
    const keystore = this.requireKeystore();
    const amountUnits = toUnits(tx.amount);
    const feeUnits = toUnits(tx.fee);
    const draft: TransactionDraft = {
      tx_type: tx.txType || "transfer",
      sender: keystore.address,
      recipient: tx.to,
      amount: canonicalAmount(amountUnits),
      amount_units: amountUnits,
      fee: canonicalAmount(feeUnits),
      fee_units: feeUnits,
      nonce: tx.nonce,
      payload: tx.payload || {},
      network_id: network.id === "mainnet" ? "mainnet" : "public-testnet",
      chain_id: network.id === "mainnet" ? "picoin-mainnet-v1" : "picoin-public-testnet-v018",
      timestamp: new Date().toISOString(),
    };
    return this.signer.sign(draft, secrets.privateKey, keystore.publicKey);
  }

  private saveMaterial(material: KeyMaterial, password: string, network: NetworkConfig): EncryptedKeystore {
    const address = this.addressEncoder.fromPublicKey(material.publicKey);
    const keystore = encryptKeystore(
      {
        privateKey: material.privateKey,
        seedPhrase: material.seedPhrase,
      },
      password,
      {
        address,
        publicKey: material.publicKey,
        networkId: network.id,
        chainId: network.id === "mainnet" ? "picoin-mainnet-v1" : "picoin-public-testnet-v018",
      },
    );
    fs.mkdirSync(path.dirname(this.keystorePath), { recursive: true });
    fs.writeFileSync(this.keystorePath, JSON.stringify(keystore, null, 2), { encoding: "utf-8", mode: 0o600 });
    return keystore;
  }

  private readKeystore(): EncryptedKeystore | null {
    if (!fs.existsSync(this.keystorePath)) {
      return null;
    }
    return JSON.parse(fs.readFileSync(this.keystorePath, "utf-8")) as EncryptedKeystore;
  }

  private requireKeystore(): EncryptedKeystore {
    const keystore = this.readKeystore();
    if (!keystore) {
      throw new Error("wallet has not been created or imported");
    }
    return keystore;
  }

  private requireUnlockedSecrets(): PlainSecrets {
    if (!this.secrets) {
      throw new Error("wallet is locked");
    }
    return this.secrets;
  }

  private summaryFromKeystore(keystore: EncryptedKeystore): WalletSummary {
    return {
      hasWallet: true,
      locked: !this.secrets,
      address: keystore.address,
      publicKey: keystore.publicKey,
      networkId: keystore.networkId,
      chainId: keystore.chainId,
    };
  }
}

