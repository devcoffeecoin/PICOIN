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
      network: keystore?.network,
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

  importFromJson(walletJson: string, password: string, network: NetworkConfig): WalletSummary {
    const parsed = parseWalletJson(walletJson);
    if (isEncryptedKeystoreJson(parsed)) {
      const keystore = this.normalizeImportedKeystore(parsed, network);
      const secrets = decryptKeystore(keystore, password);
      this.assertSecretsMatchKeystore(secrets, keystore);
      this.writeKeystore(keystore);
      this.secrets = secrets;
      return { ...this.summaryFromKeystore(keystore), locked: false };
    }

    const material = this.materialFromPlainWallet(parsed);
    const address = this.addressEncoder.fromPublicKey(material.publicKey);
    const suppliedAddress = stringField(parsed.address) || stringField(parsed.account_id);
    if (suppliedAddress && suppliedAddress !== address) {
      throw new Error("wallet JSON address does not match its private key");
    }
    const keystore = this.saveMaterialWithMetadata(material, password, {
      address,
      publicKey: material.publicKey,
      network: stringField(parsed.network_id) || stringField(parsed.network) || network.network,
      chainId: stringOrNumberField(parsed.chain_id) ?? stringOrNumberField(parsed.chainId) ?? network.chainId,
    });
    this.secrets = { privateKey: material.privateKey, seedPhrase: material.seedPhrase };
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
      network_id: network.network,
      chain_id: network.chainId,
      timestamp: new Date().toISOString(),
    };
    return this.signer.sign(draft, secrets.privateKey, keystore.publicKey);
  }

  private saveMaterial(material: KeyMaterial, password: string, network: NetworkConfig): EncryptedKeystore {
    const address = this.addressEncoder.fromPublicKey(material.publicKey);
    return this.saveMaterialWithMetadata(material, password, {
      address,
      publicKey: material.publicKey,
      network: network.network,
      chainId: network.chainId,
    });
  }

  private saveMaterialWithMetadata(
    material: KeyMaterial,
    password: string,
    metadata: Pick<EncryptedKeystore, "address" | "publicKey" | "network" | "chainId">,
  ): EncryptedKeystore {
    const keystore = encryptKeystore(
      {
        privateKey: material.privateKey,
        seedPhrase: material.seedPhrase,
      },
      password,
      metadata,
    );
    this.writeKeystore(keystore);
    return keystore;
  }

  private writeKeystore(keystore: EncryptedKeystore): void {
    fs.mkdirSync(path.dirname(this.keystorePath), { recursive: true });
    fs.writeFileSync(this.keystorePath, JSON.stringify(keystore, null, 2), { encoding: "utf-8", mode: 0o600 });
  }

  private readKeystore(): EncryptedKeystore | null {
    if (!fs.existsSync(this.keystorePath)) {
      return null;
    }
    const parsed = JSON.parse(fs.readFileSync(this.keystorePath, "utf-8")) as EncryptedKeystore & {
      networkId?: string;
    };
    if (!parsed.network && parsed.networkId) {
      parsed.network = parsed.networkId;
    }
    return parsed;
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
      network: keystore.network,
      chainId: keystore.chainId,
    };
  }

  private materialFromPlainWallet(wallet: WalletJson): KeyMaterial {
    const privateKey = stringField(wallet.private_key) || stringField(wallet.privateKey);
    const seedPhrase = stringField(wallet.seedPhrase) || stringField(wallet.seed_phrase) || stringField(wallet.mnemonic);
    const publicKey = stringField(wallet.public_key) || stringField(wallet.publicKey);
    let material: KeyMaterial;

    if (privateKey) {
      material = this.keyProvider.importFromPrivateKey(privateKey);
      if (seedPhrase) {
        const seedMaterial = this.keyProvider.importFromSeed(seedPhrase);
        if (seedMaterial.publicKey !== material.publicKey) {
          throw new Error("wallet JSON seed phrase does not match its private key");
        }
        material = { ...material, seedPhrase };
      }
    } else if (seedPhrase) {
      material = this.keyProvider.importFromSeed(seedPhrase);
    } else {
      throw new Error("wallet JSON must contain private_key, privateKey, seedPhrase, or seed_phrase");
    }

    if (publicKey && publicKey !== material.publicKey) {
      throw new Error("wallet JSON public key does not match its private key");
    }
    return material;
  }

  private normalizeImportedKeystore(wallet: WalletJson, network: NetworkConfig): EncryptedKeystore {
    const version = Number(wallet.version ?? 1);
    if (version !== 1) {
      throw new Error("unsupported wallet keystore version");
    }
    if (wallet.cipher && wallet.cipher !== "aes-256-gcm") {
      throw new Error("unsupported wallet keystore cipher");
    }
    if (wallet.kdf && wallet.kdf !== "pbkdf2-sha256") {
      throw new Error("unsupported wallet keystore kdf");
    }
    return {
      version: 1,
      cipher: "aes-256-gcm",
      kdf: "pbkdf2-sha256",
      iterations: Number(wallet.iterations || 310_000),
      salt: requiredString(wallet.salt, "salt"),
      iv: requiredString(wallet.iv, "iv"),
      tag: requiredString(wallet.tag, "tag"),
      ciphertext: requiredString(wallet.ciphertext, "ciphertext"),
      address: requiredString(wallet.address, "address"),
      publicKey: requiredString(wallet.publicKey ?? wallet.public_key, "publicKey"),
      network: stringField(wallet.network) || stringField(wallet.network_id) || network.network,
      chainId: stringOrNumberField(wallet.chainId) ?? stringOrNumberField(wallet.chain_id) ?? network.chainId,
      createdAt: stringField(wallet.createdAt) || stringField(wallet.created_at) || new Date().toISOString(),
    };
  }

  private assertSecretsMatchKeystore(secrets: PlainSecrets, keystore: EncryptedKeystore): void {
    const material = secrets.privateKey
      ? this.keyProvider.importFromPrivateKey(secrets.privateKey)
      : secrets.seedPhrase
        ? this.keyProvider.importFromSeed(secrets.seedPhrase)
        : null;
    if (!material) {
      throw new Error("wallet keystore does not contain a private key or seed phrase");
    }
    if (material.publicKey !== keystore.publicKey) {
      throw new Error("wallet keystore public key does not match its private key");
    }
    const address = this.addressEncoder.fromPublicKey(material.publicKey);
    if (address !== keystore.address) {
      throw new Error("wallet keystore address does not match its private key");
    }
    if (secrets.privateKey && secrets.seedPhrase) {
      const seedMaterial = this.keyProvider.importFromSeed(secrets.seedPhrase);
      if (seedMaterial.publicKey !== material.publicKey) {
        throw new Error("wallet keystore seed phrase does not match its private key");
      }
    }
  }
}

type WalletJson = Record<string, unknown>;

function parseWalletJson(walletJson: string): WalletJson {
  try {
    const parsed = JSON.parse(walletJson) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("wallet JSON must be an object");
    }
    return parsed as WalletJson;
  } catch (error) {
    if (error instanceof SyntaxError) {
      throw new Error("invalid wallet JSON");
    }
    throw error;
  }
}

function isEncryptedKeystoreJson(wallet: WalletJson): boolean {
  return Boolean(wallet.ciphertext && wallet.salt && wallet.iv);
}

function stringField(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function stringOrNumberField(value: unknown): string | number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return stringField(value);
}

function requiredString(value: unknown, field: string): string {
  const parsed = stringField(value);
  if (!parsed) {
    throw new Error(`wallet JSON is missing ${field}`);
  }
  return parsed;
}
