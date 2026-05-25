import nacl from "tweetnacl";

const KEY_PREFIX = "ed25519:";
const HEX_DIGITS = "0123456789ABCDEF";

function base64UrlEncode(value: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < value.length; i += 1) {
    binary += String.fromCharCode(value[i]);
  }
  const base64 = btoa(binary);
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlDecode(value: string): Uint8Array {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  return new Uint8Array(Array.from(binary, (ch) => ch.charCodeAt(0)));
}

export function encodeKey(raw: Uint8Array): string {
  return `${KEY_PREFIX}${base64UrlEncode(raw)}`;
}

export function decodeKey(value: string): Uint8Array {
  if (!value.startsWith(KEY_PREFIX)) {
    throw new Error("Key must be prefixed with ed25519:");
  }
  return base64UrlDecode(value.slice(KEY_PREFIX.length));
}

export function generateKeypair() {
  const keypair = nacl.sign.keyPair();
  const privateKey = encodeKey(keypair.secretKey.subarray(0, 32));
  const publicKey = encodeKey(keypair.publicKey);
  return { privateKey, publicKey };
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }

  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }

  const sortedKeys = Object.keys(value as Record<string, unknown>).sort();
  const entries = sortedKeys.map((key) => {
    const val = (value as Record<string, unknown>)[key];
    return `${JSON.stringify(key)}:${canonicalJson(val)}`;
  });
  return `{${entries.join(",")}}`;
}

export function buildCommitSignaturePayload(payload: {
  taskId: string;
  minerId: string;
  rangeStart: number;
  rangeEnd: number;
  algorithm: string;
  resultHash: string;
  merkleRoot: string;
  signedAt: string;
  txMerkleRoot?: string;
  mempoolSnapshotId?: string;
  selectedTxHashesHash?: string;
  txCount?: number;
  txFeeTotalUnits?: number;
  chainId?: string | number;
  networkId?: string;
}) {
  const result: Record<string, unknown> = {
    algorithm: payload.algorithm,
    merkle_root: payload.merkleRoot,
    miner_id: payload.minerId,
    range_end: payload.rangeEnd,
    range_start: payload.rangeStart,
    result_hash: payload.resultHash,
    signed_at: payload.signedAt,
    task_id: payload.taskId,
  };

  if (payload.txMerkleRoot !== undefined) {
    result.tx_merkle_root = payload.txMerkleRoot;
    result.mempool_snapshot_id = payload.mempoolSnapshotId;
    result.selected_tx_hashes_hash = payload.selectedTxHashesHash;
    result.tx_count = Number(payload.txCount || 0);
    result.tx_fee_total_units = Number(payload.txFeeTotalUnits || 0);
  }
  if (payload.chainId !== undefined) {
    result.chain_id = payload.chainId;
  }
  if (payload.networkId !== undefined) {
    result.network_id = payload.networkId;
  }
  return result;
}

export function buildRevealSignaturePayload(payload: {
  taskId: string;
  minerId: string;
  merkleRoot: string;
  challengeSeed: string;
  signedAt: string;
  txMerkleRoot?: string;
  mempoolSnapshotId?: string;
  selectedTxHashesHash?: string;
}) {
  const result: Record<string, unknown> = {
    challenge_seed: payload.challengeSeed,
    merkle_root: payload.merkleRoot,
    miner_id: payload.minerId,
    signed_at: payload.signedAt,
    task_id: payload.taskId,
  };
  if (payload.txMerkleRoot !== undefined) {
    result.tx_merkle_root = payload.txMerkleRoot;
    result.mempool_snapshot_id = payload.mempoolSnapshotId;
    result.selected_tx_hashes_hash = payload.selectedTxHashesHash;
  }
  return result;
}

export function signPayload(privateKey: string, payload: Record<string, unknown>): string {
  const seed = decodeKey(privateKey);
  const keypair = nacl.sign.keyPair.fromSeed(seed);
  const message = new TextEncoder().encode(canonicalJson(payload));
  const signature = nacl.sign.detached(message, keypair.secretKey);
  return base64UrlEncode(signature);
}

function floatMod(x: number): number {
  return x - Math.floor(x);
}

function bbpSeries(j: number, n: number): number {
  let total = 0;
  for (let k = 0; k <= n; k += 1) {
    const denominator = 8 * k + j;
    total = floatMod(total + Math.pow(16, n - k) / denominator);
  }
  let k = n + 1;
  while (true) {
    const denominator = 8 * k + j;
    const term = Math.pow(16, n - k) / denominator;
    if (term < 1e-17) {
      break;
    }
    total = floatMod(total + term);
    k += 1;
  }
  return floatMod(total);
}

function bbpHexDigit(zeroBasedPosition: number): string {
  const value = floatMod(
    4 * bbpSeries(1, zeroBasedPosition) - 2 * bbpSeries(4, zeroBasedPosition) - bbpSeries(5, zeroBasedPosition) - bbpSeries(6, zeroBasedPosition)
  );
  return HEX_DIGITS[Math.min(15, Math.floor(16 * value))];
}

export async function calculatePiSegment(
  rangeStart: number,
  rangeEnd: number,
  algorithm: string,
  speed: number
): Promise<string> {
  if (algorithm !== "bbp_hex_v1") {
    throw new Error("Only bbp_hex_v1 algorithm is supported by the miner GUI.");
  }

  let segment = "";
  const throttle = Math.max(1, 16 - speed);

  for (let position = rangeStart; position <= rangeEnd; position += 1) {
    segment += bbpHexDigit(position - 1);
    if (position % throttle === 0) {
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  }

  return segment;
}

async function sha256Hex(value: string): Promise<string> {
  const buffer = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  const bytes = new Uint8Array(digest);
  return Array.from(bytes)
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function canonicalJsonForHash(value: Record<string, unknown>): string {
  return canonicalJson(value);
}

export async function hashResult(segment: string, rangeStart: number, rangeEnd: number, algorithm: string): Promise<string> {
  return await sha256Hex(
    canonicalJsonForHash({
      algorithm,
      range_end: rangeEnd,
      range_start: rangeStart,
      segment,
    })
  );
}

export async function leafHash(position: number, digit: string): Promise<string> {
  return await sha256Hex(canonicalJsonForHash({ digit: digit.toUpperCase(), position }));
}

export async function parentHash(left: string, right: string): Promise<string> {
  return await sha256Hex(canonicalJsonForHash({ left, right }));
}

export async function merkleRoot(segment: string, rangeStart: number): Promise<string> {
  const layers = await buildMerkleLayers(segment, rangeStart);
  return layers[layers.length - 1][0];
}

async function buildMerkleLayers(segment: string, rangeStart: number): Promise<string[][]> {
  const leaves: string[] = [];
  for (let index = 0; index < segment.length; index += 1) {
    leaves.push(await leafHash(rangeStart + index, segment[index]));
  }

  const layers = [leaves];
  while (layers[layers.length - 1].length > 1) {
    const current = layers[layers.length - 1];
    const nextLayer: string[] = [];
    for (let i = 0; i < current.length; i += 2) {
      const left = current[i];
      const right = i + 1 < current.length ? current[i + 1] : left;
      nextLayer.push(await parentHash(left, right));
    }
    layers.push(nextLayer);
  }
  return layers;
}

export async function merkleProof(segment: string, rangeStart: number, position: number): Promise<Array<{ side: "left" | "right"; hash: string }>> {
  if (position < rangeStart || position >= rangeStart + segment.length) {
    throw new Error("Position is outside segment range");
  }

  const layers = await buildMerkleLayers(segment, rangeStart);
  let index = position - rangeStart;
  const proof: Array<{ side: "left" | "right"; hash: string }> = [];

  for (let layer = 0; layer < layers.length - 1; layer += 1) {
    const current = layers[layer];
    let siblingIndex = index % 2 === 0 ? index + 1 : index - 1;
    if (siblingIndex >= current.length) {
      siblingIndex = index;
    }
    proof.push({
      side: siblingIndex < index ? "left" : "right",
      hash: current[siblingIndex],
    });
    index = Math.floor(index / 2);
  }

  return proof;
}

export function formatTimestamp() {
  return new Date().toISOString();
}
