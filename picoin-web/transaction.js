const explorerConfig = window.PICOIN_EXPLORER_CONFIG || {};

function cleanUrl(value) {
  return String(value || "").replace(/\/$/, "");
}

const apiBaseUrl = cleanUrl(explorerConfig.apiBaseUrl || window.location.origin);
const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmt(value, digits = 5) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString();
}

function statusClass(status) {
  if (status === "confirmed") return "ok";
  if (status === "rejected" || status === "failed" || status === "expired") return "bad";
  return "warn";
}

async function fetchJson(path) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    headers: { Accept: "application/json" },
    mode: "cors",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || response.statusText || `Error ${response.status}`);
  }
  return payload;
}

async function fetchTransaction(hash) {
  const encoded = encodeURIComponent(hash);
  try {
    return await fetchJson(`/tx/${encoded}`);
  } catch (error) {
    if (!/not found|404/i.test(error.message)) throw error;
    return fetchJson(`/transactions/${encoded}`);
  }
}

function readHashFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return (params.get("hash") || params.get("tx") || "").trim();
}

function updateUrl(hash) {
  const url = new URL(window.location.href);
  url.searchParams.set("hash", hash);
  window.history.replaceState({}, "", url.toString());
}

function field(label, value, options = {}) {
  const rendered = options.date ? formatDate(value) : value;
  const className = options.mono ? "mono detail-value" : "detail-value";
  return `
    <div class="tx-field">
      <dt>${escapeHtml(label)}</dt>
      <dd class="${className}">${escapeHtml(rendered ?? "-")}</dd>
    </div>
  `;
}

function renderPayload(payload) {
  const value = payload && Object.keys(payload).length ? JSON.stringify(payload, null, 2) : "{}";
  return `<pre class="tx-json">${escapeHtml(value)}</pre>`;
}

function renderTx(tx) {
  $("txTitle").textContent = tx.tx_hash || "Transaction";
  const badge = $("txStatusBadge");
  badge.textContent = tx.status || "unknown";
  badge.className = `status-pill ${statusClass(tx.status)}`;

  $("txDetail").innerHTML = `
    <article class="tx-detail-card">
      <header>
        <span>Hash</span>
        <strong class="mono">${escapeHtml(tx.tx_hash)}</strong>
      </header>
      <dl class="tx-field-grid">
        ${field("Status", tx.status)}
        ${field("Type", tx.tx_type)}
        ${field("Amount", `${fmt(tx.amount)} PI`)}
        ${field("Fee", `${fmt(tx.fee)} PI`)}
        ${field("Nonce", tx.nonce)}
        ${field("Block", tx.block_height ?? "-")}
        ${field("Network", tx.network_id)}
        ${field("Chain", tx.chain_id)}
        ${field("Created", tx.created_at, { date: true })}
        ${field("Updated", tx.updated_at, { date: true })}
        ${field("Timestamp", tx.timestamp, { date: true })}
        ${field("Expires", tx.expires_at, { date: true })}
      </dl>
    </article>

    <article class="tx-detail-card">
      <header>
        <span>Participants</span>
      </header>
      <dl class="tx-field-grid single">
        ${field("Sender", tx.sender, { mono: true })}
        ${field("Recipient", tx.recipient || "-", { mono: true })}
        ${field("Public Key", tx.public_key, { mono: true })}
        ${field("Signature", tx.signature, { mono: true })}
      </dl>
    </article>

    <article class="tx-detail-card">
      <header>
        <span>Settlement</span>
      </header>
      <dl class="tx-field-grid">
        ${field("Propagated", tx.propagated ? "yes" : "no")}
        ${field("Selected task", tx.selected_task_id || "-", { mono: true })}
        ${field("Snapshot", tx.mempool_snapshot_id || "-", { mono: true })}
        ${field("Selected block", tx.selected_block_height ?? "-")}
        ${field("Confirmed at", tx.confirmed_at, { date: true })}
        ${field("Confirmations", tx.confirmations ?? "-")}
        ${field("Released at", tx.released_at, { date: true })}
        ${field("Failure reason", tx.failure_reason || tx.rejection_reason || "-")}
      </dl>
    </article>

    <article class="tx-detail-card">
      <header>
        <span>Payload</span>
      </header>
      ${renderPayload(tx.payload || {})}
    </article>
  `;
}

function renderError(message) {
  $("txError").innerHTML = `<div class="api-error">${escapeHtml(message)}</div>`;
  $("txStatusBadge").textContent = "Not found";
  $("txStatusBadge").className = "status-pill bad";
}

async function openTransaction(hash) {
  const txHash = String(hash || "").trim();
  if (!/^[a-f0-9]{64}$/i.test(txHash)) {
    renderError("Enter a valid 64-character transaction hash.");
    return;
  }
  $("txError").innerHTML = "";
  $("txStatusBadge").textContent = "Loading";
  $("txStatusBadge").className = "status-pill warn";
  $("txHashInput").value = txHash;
  updateUrl(txHash);
  const tx = await fetchTransaction(txHash);
  renderTx(tx);
}

$("txLookupButton")?.addEventListener("click", () => {
  openTransaction($("txHashInput").value).catch((error) => renderError(error.message));
});

$("txHashInput")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    openTransaction($("txHashInput").value).catch((error) => renderError(error.message));
  }
});

const initialHash = readHashFromUrl();
if (initialHash) {
  openTransaction(initialHash).catch((error) => renderError(error.message));
}
