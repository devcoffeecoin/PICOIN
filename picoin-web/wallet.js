const STORE_KEY = "picoin:web-wallet";
const PICOIN_UNIT = 1_000_000n;
const PICOIN_DECIMALS = 6;
const CHAIN_ID = "picoin-public-testnet-v018";
const NETWORKS = {
  "public-testnet": "https://api.picoin.science",
  localhost: "http://127.0.0.1:8000",
};

const encoder = new TextEncoder();
const els = {
  networkSelect: document.getElementById("networkSelect"),
  apiUrl: document.getElementById("apiUrl"),
  apiDisplay: document.getElementById("apiDisplay"),
  networkBadge: document.getElementById("networkBadge"),
  latestBlock: document.getElementById("latestBlock"),
  syncStatus: document.getElementById("syncStatus"),
  refreshNetwork: document.getElementById("refreshNetwork"),
  createWallet: document.getElementById("createWallet"),
  importWallet: document.getElementById("importWallet"),
  exportWallet: document.getElementById("exportWallet"),
  copyAddress: document.getElementById("copyAddress"),
  walletBadge: document.getElementById("walletBadge"),
  walletAddress: document.getElementById("walletAddress"),
  walletBalance: document.getElementById("walletBalance"),
  walletNonce: document.getElementById("walletNonce"),
  lastTxHash: document.getElementById("lastTxHash"),
  sendForm: document.getElementById("sendForm"),
  txTo: document.getElementById("txTo"),
  txAmount: document.getElementById("txAmount"),
  txFee: document.getElementById("txFee"),
  txResult: document.getElementById("txResult"),
  refreshHistory: document.getElementById("refreshHistory"),
  historyList: document.getElementById("historyList"),
};

function base64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function keyBody(value) {
  return String(value || "").replace("ed25519:", "");
}

function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
    .join(",")}}`;
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

function currentApi() {
  return els.apiUrl.value.replace(/\/+$/, "");
}

function api(path) {
  return `${currentApi()}${path}`;
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

async function fetchFirst(paths, options = {}) {
  let lastError;
  for (const path of paths) {
    try {
      return await fetchJson(path, options);
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

function getWallet() {
  const raw = localStorage.getItem(STORE_KEY);
  return raw ? JSON.parse(raw) : null;
}

function setWallet(wallet) {
  localStorage.setItem(STORE_KEY, JSON.stringify(wallet));
  renderWallet();
}

function updateBadge(element, text, mode = "") {
  element.className = `status-pill ${mode}`.trim();
  element.textContent = text;
}

function renderWallet() {
  const wallet = getWallet();
  updateBadge(els.walletBadge, wallet ? "Ready" : "No wallet", wallet ? "ok" : "");
  els.walletAddress.textContent = wallet?.address || "Create or import a wallet";
  els.walletBalance.textContent = "-";
  els.walletNonce.textContent = "-";
  els.lastTxHash.textContent = localStorage.getItem(`${STORE_KEY}:last_tx`) || "-";
}

async function refreshNetwork() {
  els.apiDisplay.textContent = currentApi();
  updateBadge(els.networkBadge, "checking");
  try {
    const [health, sync] = await Promise.all([
      fetchJson("/health").catch(() => ({})),
      fetchJson("/node/sync-status").catch(() => ({})),
    ]);
    const healthy = health.status === "ok" || health.status === "degraded";
    updateBadge(els.networkBadge, healthy ? health.status : "offline", healthy ? "ok" : "bad");
    els.latestBlock.textContent = sync.effective_latest_block_height ?? sync.latest_block_height ?? health.latest_block_height ?? "-";
    els.syncStatus.textContent = sync.sync_mode || health.status || "-";
  } catch (error) {
    updateBadge(els.networkBadge, "offline", "bad");
    els.syncStatus.textContent = error.message;
  }
}

async function refreshWallet() {
  const wallet = getWallet();
  renderWallet();
  if (!wallet) return;
  try {
    const [account, nonce] = await Promise.all([
      fetchFirst([`/wallet/balance/${wallet.address}`, `/accounts/${wallet.address}`]).catch(() => ({ balance: 0 })),
      fetchJson(`/wallet/${wallet.address}/nonce`).catch(() => ({ next_nonce: 1 })),
    ]);
    els.walletBalance.textContent = `${account.balance ?? 0} PI`;
    els.walletNonce.textContent = nonce.next_nonce || 1;
  } catch (error) {
    updateBadge(els.walletBadge, error.message, "bad");
  }
}

async function refreshHistory() {
  const wallet = getWallet();
  els.historyList.innerHTML = "";
  if (!wallet) {
    els.historyList.innerHTML = '<p class="empty">Create or import a wallet to see activity.</p>';
    return;
  }
  const history = await fetchFirst([`/transactions/${wallet.address}`, `/accounts/${wallet.address}/history?limit=25`]).catch(() => []);
  const rows = Array.isArray(history) ? history : history.transactions || history.history || [];
  els.historyList.innerHTML = rows.length
    ? rows
        .map((item) => {
          const amount = Number(item.amount || 0);
          const detail = item.tx_hash || item.description || item.related_id || "";
          return `<div class="wallet-history-item"><span>${item.entry_type || item.tx_type || "transaction"}</span><span>${detail}</span><strong class="${amount >= 0 ? "positive" : "negative"}">${amount}</strong></div>`;
        })
        .join("")
    : '<p class="empty">No transaction history yet.</p>';
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
    network_id: els.networkSelect.value,
    chain_id: CHAIN_ID,
    created_at: new Date().toISOString(),
  };
  setWallet(wallet);
  await refreshWallet();
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
    chain_id: CHAIN_ID,
    fee: canonicalAmount(feeUnits),
    fee_units: Number(feeUnits),
    network_id: els.networkSelect.value,
    nonce,
    payload: {},
    recipient: els.txTo.value.trim().toUpperCase(),
    sender: wallet.address,
    timestamp: new Date().toISOString(),
    tx_type: "transfer",
  };
  const signature = await signPayload(wallet, unsignedPayload);
  const tx_hash = await transactionHash(unsignedPayload, wallet.public_key);
  const body = JSON.stringify({ ...unsignedPayload, public_key: wallet.public_key, signature, tx_hash });
  const submitted = await fetchFirst(["/tx/send", "/transactions/submit", "/tx/submit"], { method: "POST", body });
  localStorage.setItem(`${STORE_KEY}:last_tx`, submitted.tx_hash || tx_hash);
  els.lastTxHash.textContent = submitted.tx_hash || tx_hash;
  els.txResult.textContent = JSON.stringify(submitted, null, 2);
  await refreshWallet();
  await refreshHistory();
}

els.networkSelect.addEventListener("change", () => {
  els.apiUrl.value = NETWORKS[els.networkSelect.value];
  refreshNetwork();
  refreshWallet();
});
els.apiUrl.addEventListener("change", refreshNetwork);
els.refreshNetwork.addEventListener("click", refreshNetwork);
els.createWallet.addEventListener("click", () => createWallet().catch((error) => updateBadge(els.walletBadge, error.message, "bad")));
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
els.importWallet.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  const wallet = JSON.parse(await file.text());
  if (!wallet.address && wallet.public_key) wallet.address = await deriveAddress(wallet.public_key);
  setWallet(wallet);
  await refreshWallet();
  await refreshHistory();
});
els.copyAddress.addEventListener("click", async () => {
  const wallet = getWallet();
  if (!wallet) return;
  await navigator.clipboard.writeText(wallet.address);
  els.copyAddress.textContent = "Copied";
  setTimeout(() => (els.copyAddress.textContent = "Copy"), 1200);
});
els.sendForm.addEventListener("submit", (event) => submitTransaction(event).catch((error) => (els.txResult.textContent = error.message)));
els.refreshHistory.addEventListener("click", refreshHistory);

renderWallet();
refreshNetwork();
refreshWallet();
refreshHistory();
