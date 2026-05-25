import crypto from "node:crypto";

export interface PlainSecrets {
  privateKey: string;
  seedPhrase?: string;
}

export interface EncryptedKeystore {
  version: 1;
  cipher: "aes-256-gcm";
  kdf: "pbkdf2-sha256";
  iterations: number;
  salt: string;
  iv: string;
  tag: string;
  ciphertext: string;
  address: string;
  publicKey: string;
  networkId: string;
  chainId: string | number;
  createdAt: string;
}

const ITERATIONS = 310_000;

export function encryptKeystore(
  secrets: PlainSecrets,
  password: string,
  metadata: Pick<EncryptedKeystore, "address" | "publicKey" | "networkId" | "chainId">,
): EncryptedKeystore {
  assertPassword(password);
  const salt = crypto.randomBytes(16);
  const iv = crypto.randomBytes(12);
  const key = crypto.pbkdf2Sync(password, salt, ITERATIONS, 32, "sha256");
  const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
  const ciphertext = Buffer.concat([
    cipher.update(JSON.stringify(secrets), "utf-8"),
    cipher.final(),
  ]);
  const tag = cipher.getAuthTag();
  return {
    version: 1,
    cipher: "aes-256-gcm",
    kdf: "pbkdf2-sha256",
    iterations: ITERATIONS,
    salt: salt.toString("base64url"),
    iv: iv.toString("base64url"),
    tag: tag.toString("base64url"),
    ciphertext: ciphertext.toString("base64url"),
    address: metadata.address,
    publicKey: metadata.publicKey,
    networkId: metadata.networkId,
    chainId: metadata.chainId,
    createdAt: new Date().toISOString(),
  };
}

export function decryptKeystore(keystore: EncryptedKeystore, password: string): PlainSecrets {
  assertPassword(password);
  const key = crypto.pbkdf2Sync(
    password,
    Buffer.from(keystore.salt, "base64url"),
    keystore.iterations,
    32,
    "sha256",
  );
  const decipher = crypto.createDecipheriv("aes-256-gcm", key, Buffer.from(keystore.iv, "base64url"));
  decipher.setAuthTag(Buffer.from(keystore.tag, "base64url"));
  const plaintext = Buffer.concat([
    decipher.update(Buffer.from(keystore.ciphertext, "base64url")),
    decipher.final(),
  ]).toString("utf-8");
  return JSON.parse(plaintext) as PlainSecrets;
}

function assertPassword(password: string): void {
  if (!password || password.length < 8) {
    throw new Error("password must be at least 8 characters");
  }
}
