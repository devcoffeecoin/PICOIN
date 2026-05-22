const STORE_KEY = "picoin:web-wallet";
const PICOIN_UNIT = 1_000_000n;
const PICOIN_DECIMALS = 6;
const CHAIN_ID = "picoin-public-testnet-v018";
const NETWORKS = {
  "public-testnet": "/picoin-api",
  localhost: "http://127.0.0.1:8000",
};
const NETWORK_LABELS = {
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
  const base = currentApi();
  if (!base || base === "/") return path;
  return `${base}${path}`;
}

async function fetchJson(path, options = {}) {
  const response = await fetch(api(path), {
    ...options,
    mode: "cors",
    headers: { "content-type": "application/json", ...(options.headers || {}) },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(body.detail || text || response.statusText);
  return body;
}

async function fetchFirst(paths, options = {}) {
  const errors = [];
  for (const path of paths) {
    try {
      return await fetchJson(path, options);
    } catch (error) {
      errors.push({ path, error: error.message });
    }
  }
  const errorDetails = errors.map(e => `${e.path}: ${e.error}`).join(" | ");
  throw new Error(`All endpoints failed: ${errorDetails}`);
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
  els.apiDisplay.textContent = NETWORK_LABELS[els.networkSelect.value] || currentApi();
  updateBadge(els.networkBadge, "checking");
  const [healthResult, syncResult, configResult] = await Promise.allSettled([
    fetchJson("/health"),
    fetchJson("/node/sync-status"),
    fetchJson("/network/config"),
  ]);
  const health = healthResult.status === "fulfilled" ? healthResult.value : null;
  const sync = syncResult.status === "fulfilled" ? syncResult.value : null;
  const config = configResult.status === "fulfilled" ? configResult.value : null;
  const status = health?.status || (sync ? "ok" : null);
  
  // Check network configuration compatibility
  if (config) {
    const expectedNetwork = els.networkSelect.value;
    const expectedChain = CHAIN_ID;
    if (config.network_id !== expectedNetwork || config.chain_id !== expectedChain) {
      console.warn("⚠️ Network mismatch detected:", {
        expected: { network_id: expectedNetwork, chain_id: expectedChain },
        actual: { network_id: config.network_id, chain_id: config.chain_id },
      });
    }
    console.log("Server network config:", config);
  }
  
  if (status) {
    const mode = status === "ok" || status === "degraded" ? "ok" : "bad";
    updateBadge(els.networkBadge, status, mode);
    els.latestBlock.textContent = sync?.effective_latest_block_height ?? sync?.latest_block_height ?? health?.latest_block_height ?? "-";
    els.syncStatus.textContent = sync?.sync_mode || health?.status || "connected";
  } else {
    const error = healthResult.reason || syncResult.reason || new Error("API unavailable");
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
  
  // Build transaction payload
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
  
  // Sign and calculate tx_hash
  const signature = await signPayload(wallet, unsignedPayload);
  const tx_hash = await transactionHash(unsignedPayload, wallet.public_key);
  const txPayload = { ...unsignedPayload, public_key: wallet.public_key, signature, tx_hash };
  
  // Log payload for debugging
  console.log("Submitting transaction:", {
    tx_hash,
    network_id: unsignedPayload.network_id,
    chain_id: unsignedPayload.chain_id,
    sender: unsignedPayload.sender,
    recipient: unsignedPayload.recipient,
    amount: unsignedPayload.amount,
    nonce: unsignedPayload.nonce,
    api_url: currentApi(),
  });
  
  // Submit transaction
  let submitted = null;
  let submitError = null;
  const submitEndpoints = ["/tx/send", "/transactions/submit", "/tx/submit"];
  
  for (const endpoint of submitEndpoints) {
    try {
      submitted = await fetchJson(endpoint, { method: "POST", body: JSON.stringify(txPayload) });
      console.log(`Success on ${endpoint}:`, submitted);
      break;
    } catch (error) {
      console.warn(`Failed on ${endpoint}:`, error.message);
      submitError = error;
    }
  }
  
  if (!submitted) {
    throw submitError || new Error("Failed to submit transaction to all endpoints");
  }
  
  // Verify transaction is in mempool
  console.log("Verifying transaction in mempool...");
  let verified = false;
  let verificationError = null;
  
  for (let i = 0; i < 5; i++) {
    try {
      const txCheck = await fetchJson(`/tx/${tx_hash}`).catch(() => null);
      if (txCheck && txCheck.status === "pending") {
        verified = true;
        console.log("Transaction verified in mempool:", txCheck);
        break;
      }
    } catch (error) {
      verificationError = error;
    }
    
    if (i < 4) {
      await new Promise(resolve => setTimeout(resolve, 500)); // Wait 500ms between checks
    }
  }
  
  // Store and display result
  const result = {
    success: submitted ? true : false,
    verified_in_mempool: verified,
    tx_hash,
    status: submitted.status || "unknown",
    message: verified 
      ? "Transaction submitted and verified in mempool" 
      : "Transaction was signed but may not have been accepted by mempool",
    submit_response: submitted,
    api_endpoint: currentApi(),
    network_id: unsignedPayload.network_id,
    chain_id: unsignedPayload.chain_id,
  };
  
  localStorage.setItem(`${STORE_KEY}:last_tx`, tx_hash);
  els.lastTxHash.textContent = tx_hash;
  els.txResult.textContent = JSON.stringify(result, null, 2);
  
  if (!verified) {
    console.warn("⚠️ Transaction not verified in mempool after submit!");
    updateBadge(els.walletBadge, "Tx submitted but not in mempool", "bad");
  }
  
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
