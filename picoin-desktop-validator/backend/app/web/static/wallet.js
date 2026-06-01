const STORE_KEY = "picoin:testnet:wallet";
const PICOIN_UNIT = 1_000_000n;
const PICOIN_DECIMALS = 6;
const encoder = new TextEncoder();

const els = {
  apiUrl: document.getElementById("apiUrl"),
  createWallet: document.getElementById("createWallet"),
  exportWallet: document.getElementById("exportWallet"),
  importWallet: document.getElementById("importWallet"),
  refreshWallet: document.getElementById("refreshWallet"),
  refreshHistory: document.getElementById("refreshHistory"),
  walletStatus: document.getElementById("walletStatus"),
  walletAddress: document.getElementById("walletAddress"),
  walletBalance: document.getElementById("walletBalance"),
  walletNonce: document.getElementById("walletNonce"),
  lastTxHash: document.getElementById("lastTxHash"),
  sendForm: document.getElementById("sendForm"),
  txTo: document.getElementById("txTo"),
  txAmount: document.getElementById("txAmount"),
  txFee: document.getElementById("txFee"),
  txResult: document.getElementById("txResult"),
  historyList: document.getElementById("historyList"),
};

function base64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function fromBase64Url(value) {
  const padded = value + "=".repeat((4 - (value.length % 4)) % 4);
  const binary = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

function keyBody(value) {
  return String(value || "").replace("ed25519:", "");
}

async function sha256Hex(text) {
  const hash = await crypto.subtle.digest("SHA-256", encoder.encode(text));
  return Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function deriveAddress(publicKey) {
  const body = (await sha256Hex(publicKey)).toUpperCase().slice(0, 38);
  const checksum = (await sha256Hex(body)).toUpperCase().slice(0, 8);
  return `PI${body}${checksum}`;
}

function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
    .join(",")}}`;
}

function toUnits(value) {
  const raw = String(value ?? "0").trim();
  if (!/^\d+(\.\d+)?$/.test(raw)) throw new Error("Invalid PICOIN amount.");
  const [whole, fraction = ""] = raw.split(".");
  if (fraction.length > PICOIN_DECIMALS) throw new Error(`Use at most ${PICOIN_DECIMALS} decimal places.`);
  const units = BigInt(whole) * PICOIN_UNIT + BigInt(fraction.padEnd(PICOIN_DECIMALS, "0"));
  if (units > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("Amount is too large for browser signing.");
  return units;
}

function canonicalAmount(units) {
  const sign = units < 0n ? "-" : "";
  const value = units < 0n ? -units : units;
  const whole = value / PICOIN_UNIT;
  const fraction = String(value % PICOIN_UNIT).padStart(PICOIN_DECIMALS, "0");
  return `${sign}${whole}.${fraction}`;
}

async function signPayload(wallet, payload) {
  const jwk = {
    crv: "Ed25519",
    d: keyBody(wallet.private_key),
    ext: true,
    key_ops: ["sign"],
    kty: "OKP",
    x: keyBody(wallet.public_key),
  };
  const privateKey = await crypto.subtle.importKey("jwk", jwk, { name: "Ed25519" }, false, ["sign"]);
  const signature = await crypto.subtle.sign({ name: "Ed25519" }, privateKey, encoder.encode(canonicalJson(payload)));
  return base64Url(new Uint8Array(signature));
}

async function transactionHash(unsignedPayload, publicKey) {
  return sha256Hex(canonicalJson({ public_key: publicKey, tx: unsignedPayload }));
}

function getWallet() {
  const raw = localStorage.getItem(STORE_KEY);
  return raw ? JSON.parse(raw) : null;
}

function setWallet(wallet) {
  localStorage.setItem(STORE_KEY, JSON.stringify(wallet));
  renderWallet(wallet);
}

function api(path) {
  return `${els.apiUrl.value.replace(/\/+$/, "")}${path}`;
}

async function fetchJson(path, options = {}) {
  const response = await fetch(api(path), {
    ...options,
    headers: { "content-type": "application/json", ...(options.headers || {}) },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(body.detail || text || response.statusText);
  return body;
}

function renderWallet(wallet) {
  els.walletStatus.textContent = wallet ? "Ready" : "No wallet";
  els.walletAddress.textContent = wallet?.address || "-";
  els.walletBalance.textContent = "-";
  els.walletNonce.textContent = "-";
  els.lastTxHash.textContent = localStorage.getItem(`${STORE_KEY}:last_tx`) || "-";
}

async function createWallet() {
  if (!crypto.subtle) throw new Error("WebCrypto is not available in this browser.");
  const keyPair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const privateJwk = await crypto.subtle.exportKey("jwk", keyPair.privateKey);
  const publicKey = `ed25519:${privateJwk.x}`;
  const wallet = {
    version: 1,
    name: "picoin-web-wallet",
    address: await deriveAddress(publicKey),
    public_key: publicKey,
    private_key: `ed25519:${privateJwk.d}`,
    created_at: new Date().toISOString(),
  };
  setWallet(wallet);
  await refreshWallet();
}

async function refreshWallet() {
  const wallet = getWallet();
  renderWallet(wallet);
  if (!wallet) return;
  try {
    const [account, nonce] = await Promise.all([
      fetchJson(`/accounts/${wallet.address}`).catch(() => ({ balance: 0 })),
      fetchJson(`/wallet/${wallet.address}/nonce`).catch(() => ({ next_nonce: 1 })),
    ]);
    els.walletBalance.textContent = `${account.balance || 0} PI`;
    els.walletNonce.textContent = nonce.next_nonce || 1;
  } catch (error) {
    els.walletStatus.textContent = error.message;
  }
}

async function refreshHistory() {
  const wallet = getWallet();
  els.historyList.innerHTML = "";
  if (!wallet) return;
  const history = await fetchJson(`/accounts/${wallet.address}/history?limit=25`).catch(() => []);
  els.historyList.innerHTML = history.length
    ? history
        .map((item) => {
          const amount = Number(item.amount || 0);
          return `<div class="history-item"><span>${item.entry_type}</span><span>${item.description || item.related_id || ""}</span><strong class="${amount >= 0 ? "positive" : "negative"}">${amount}</strong></div>`;
        })
        .join("")
    : "<p>No history yet.</p>";
}

async function submitTransaction(event) {
  event.preventDefault();
  const wallet = getWallet();
  if (!wallet) throw new Error("Create or import a wallet first.");
  const nonce = Number(els.walletNonce.textContent || 0) || (await fetchJson(`/wallet/${wallet.address}/nonce`)).next_nonce;
  const amountUnits = toUnits(els.txAmount.value);
  const feeUnits = toUnits(els.txFee.value || "0");
  const unsignedPayload = {
    amount: canonicalAmount(amountUnits),
    amount_units: Number(amountUnits),
    chain_id: "picoin-public-testnet-v018",
    fee: canonicalAmount(feeUnits),
    fee_units: Number(feeUnits),
    network_id: "public-testnet",
    nonce,
    payload: {},
    recipient: els.txTo.value.trim().toUpperCase(),
    sender: wallet.address,
    timestamp: new Date().toISOString(),
    tx_type: "transfer",
  };
  const signature = await signPayload(wallet, unsignedPayload);
  const tx_hash = await transactionHash(unsignedPayload, wallet.public_key);
  const submitted = await fetchJson("/transactions/submit", {
    method: "POST",
    body: JSON.stringify({ ...unsignedPayload, public_key: wallet.public_key, signature, tx_hash }),
  });
  localStorage.setItem(`${STORE_KEY}:last_tx`, submitted.tx_hash || tx_hash);
  els.lastTxHash.textContent = submitted.tx_hash || tx_hash;
  els.txResult.textContent = JSON.stringify(submitted, null, 2);
  await refreshWallet();
  await refreshHistory();
}

els.createWallet.addEventListener("click", () => createWallet().catch((error) => (els.walletStatus.textContent = error.message)));
els.refreshWallet.addEventListener("click", () => refreshWallet());
els.refreshHistory.addEventListener("click", () => refreshHistory());
els.sendForm.addEventListener("submit", (event) => submitTransaction(event).catch((error) => (els.txResult.textContent = error.message)));
els.importWallet.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  const wallet = JSON.parse(await file.text());
  if (!wallet.address && wallet.public_key) wallet.address = await deriveAddress(wallet.public_key);
  setWallet(wallet);
  await refreshWallet();
});
els.exportWallet.addEventListener("click", () => {
  const wallet = getWallet();
  if (!wallet) return;
  const blob = new Blob([JSON.stringify(wallet, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${wallet.address}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
});

renderWallet(getWallet());
refreshWallet();
refreshHistory();
