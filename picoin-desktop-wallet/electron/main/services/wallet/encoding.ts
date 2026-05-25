import crypto from "node:crypto";

export function sha256Text(value: string): string {
  return crypto.createHash("sha256").update(value, "utf-8").digest("hex");
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortCanonical(value));
}

export function base64UrlEncode(raw: Uint8Array): string {
  return Buffer.from(raw).toString("base64url");
}

export function base64UrlDecode(value: string): Buffer {
  return Buffer.from(value, "base64url");
}

export function encodeEd25519Key(raw: Uint8Array): string {
  return `ed25519:${base64UrlEncode(raw)}`;
}

export function decodeEd25519Key(value: string): Buffer {
  if (!value.startsWith("ed25519:")) {
    throw new Error("key must use ed25519:<base64url> format");
  }
  const raw = base64UrlDecode(value.slice("ed25519:".length));
  if (raw.length !== 32) {
    throw new Error("ed25519 key must contain 32 raw bytes");
  }
  return raw;
}

export function toUnits(amount: number): number {
  if (!Number.isFinite(amount)) {
    throw new Error("invalid Picoin amount");
  }
  return Math.round(amount * 1_000_000);
}

export function canonicalAmount(units: number): string {
  return (units / 1_000_000).toFixed(6);
}

function sortCanonical(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortCanonical);
  }
  if (value && typeof value === "object") {
    return Object.keys(value as Record<string, unknown>)
      .sort()
      .reduce<Record<string, unknown>>((acc, key) => {
        acc[key] = sortCanonical((value as Record<string, unknown>)[key]);
        return acc;
      }, {});
  }
  return value;
}

