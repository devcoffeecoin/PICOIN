const explorerConfig = window.PICOIN_EXPLORER_CONFIG || {};

function cleanUrl(value) {
  return String(value || "").replace(/\/$/, "");
}

const configuredNodes = Array.isArray(explorerConfig.nodes) ? explorerConfig.nodes : [];
const fallbackApiBaseUrl = cleanUrl(explorerConfig.apiBaseUrl || window.location.origin);
const nodes = (configuredNodes.length ? configuredNodes : [{ label: "Primary", url: fallbackApiBaseUrl }]).map((node, index) => ({
  label: node.label || `Node ${index + 1}`,
  url: cleanUrl(node.url || fallbackApiBaseUrl),
}));
const apiBaseUrl = cleanUrl(explorerConfig.apiBaseUrl || nodes[0]?.url || fallbackApiBaseUrl);
const refreshMs = Number(explorerConfig.refreshMs || 30000);

const state = {
  health: null,
  stats: null,
  sync: null,
  audit: null,
  consensus: null,
  reserve: null,
  treasury: null,
  blocks: [],
  validators: [],
  events: [],
  transactions: [],
  nodeStates: [],
  errors: [],
};

const $ = (id) => document.getElementById(id);

function fmt(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });
}

function shortHash(value) {
  if (!value) return "-";
  return `${value.slice(0, 10)}...${value.slice(-8)}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString();
}

function statusClass(ok) {
  return ok ? "ok" : "bad";
}

function peerCount(sync) {
  const counts = sync?.peer_counts || {};
  return `${fmt(counts.connected, 0)} / ${fmt(counts.total, 0)}`;
}

async function fetchJsonFrom(baseUrl, path) {
  const response = await fetch(`${cleanUrl(baseUrl)}${path}`, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || response.statusText || `HTTP ${response.status}`);
  }
  return payload;
}

async function fetchJson(path) {
  return fetchJsonFrom(apiBaseUrl, path);
}

async function loadEndpoint(key, path, fallback) {
  try {
    state[key] = await fetchJson(path);
  } catch (error) {
    state[key] = fallback;
    state.errors.push({ path, message: error.message });
  }
}

async function loadNodeState(node) {
  try {
    const [health, sync] = await Promise.all([
      fetchJsonFrom(node.url, "/health"),
      fetchJsonFrom(node.url, "/node/sync-status"),
    ]);
    return { ...node, health, sync, ok: health.status === "ok", error: null };
  } catch (error) {
    return { ...node, health: null, sync: null, ok: false, error: error.message };
  }
}

async function loadExplorer() {
  state.errors = [];
  await Promise.all([
    loadEndpoint("health", "/health", null),
    loadEndpoint("stats", "/stats", null),
    loadEndpoint("sync", "/node/sync-status", null),
    loadEndpoint("audit", "/audit/full", null),
    loadEndpoint("consensus", "/consensus/status", null),
    loadEndpoint("reserve", "/reserve/status", null),
    loadEndpoint("treasury", "/treasury/status", null),
    loadEndpoint("blocks", "/blocks", []),
    loadEndpoint("validators", "/validators?limit=100", []),
    loadEndpoint("events", "/events?limit=16", []),
    loadEndpoint("transactions", "/mempool?limit=40", []),
  ]);
  state.nodeStates = await Promise.all(nodes.map(loadNodeState));
  render();
}

function networkAgreement() {
  const liveNodes = state.nodeStates.filter((node) => node.sync);
  if (liveNodes.length === 0) return false;
  const [first] = liveNodes;
  return liveNodes.every((node) => {
    const sync = node.sync;
    return (
      sync.network_id === first.sync.network_id &&
      sync.chain_id === first.sync.chain_id &&
      sync.genesis_hash === first.sync.genesis_hash &&
      Number(sync.latest_block_height || 0) === Number(first.sync.latest_block_height || 0) &&
      sync.latest_block_hash === first.sync.latest_block_hash &&
      Number(sync.pending_replay_blocks || 0) === 0
    );
  });
}

function explorerReady() {
  return Boolean(
    state.health?.status === "ok" &&
      state.audit?.valid === true &&
      Number(state.sync?.pending_replay_blocks || 0) === 0 &&
      networkAgreement()
  );
}

function render() {
  renderStatus();
  renderNetwork();
  renderBlocks();
  renderTransactions();
  renderValidators();
  renderEvents();
  renderErrors();
}

function renderStatus() {
  const ready = explorerReady();
  const partial = state.health || state.sync || state.nodeStates.some((node) => node.sync);
  const apiStatus = $("apiStatus");
  apiStatus.textContent = ready ? "Network Ready" : partial ? "Degraded" : "Offline";
  apiStatus.className = `status-pill ${ready ? "ok" : partial ? "warn" : "bad"}`;

  $("metricHeight").textContent = fmt(state.sync?.latest_block_height ?? state.health?.latest_block_height, 0);
  $("metricChain").textContent = state.sync?.network_id || state.health?.network_id || "-";
  $("metricSupply").textContent = fmt(state.audit?.supply?.actual_total_balances ?? state.stats?.circulating_supply, 5);
  $("metricValidators").textContent = fmt(state.health?.database?.eligible_validators, 0);
  $("metricReserve").textContent = state.reserve?.status || fmt(state.stats?.total_science_reserve_rewards, 5);
  $("metricReplay").textContent = fmt(state.sync?.pending_replay_blocks, 0);
}

function renderNetwork() {
  const ready = explorerReady();
  const sync = state.sync || {};
  const auditOk = state.audit?.valid === true;
  const nodeOkCount = state.nodeStates.filter((node) => node.ok && node.sync).length;
  $("networkSummary").innerHTML = `
    <article>
      <span>Consensus View</span>
      <strong class="${ready ? "good-text" : "warn-text"}">${ready ? "Aligned" : "Needs attention"}</strong>
    </article>
    <article>
      <span>Tip</span>
      <strong title="${escapeHtml(sync.latest_block_hash)}">${fmt(sync.latest_block_height, 0)} / ${escapeHtml(shortHash(sync.latest_block_hash))}</strong>
    </article>
    <article>
      <span>Audit</span>
      <strong class="${auditOk ? "good-text" : "bad-text"}">${auditOk ? "Valid" : "Review"}</strong>
    </article>
    <article>
      <span>Connected Nodes</span>
      <strong>${fmt(nodeOkCount, 0)} / ${fmt(state.nodeStates.length, 0)}</strong>
    </article>
    <article>
      <span>Pending Txs</span>
      <strong>${fmt((state.transactions || []).filter((tx) => tx.status === "pending").length, 0)}</strong>
    </article>
    <article>
      <span>Fork Groups</span>
      <strong>${fmt(state.consensus?.fork_group_count, 0)}</strong>
    </article>
  `;

  if (!state.nodeStates.length) {
    $("nodesTable").innerHTML = `<tr><td colspan="7" class="empty">Waiting for peers</td></tr>`;
    return;
  }

  $("nodesTable").innerHTML = state.nodeStates
    .map((node) => {
      const syncState = node.sync || {};
      const ok = node.ok && Number(syncState.pending_replay_blocks || 0) === 0;
      return `
        <tr>
          <td>
            <strong>${escapeHtml(node.label)}</strong>
            <div class="muted mono">${escapeHtml(node.url)}</div>
          </td>
          <td><span class="status-pill ${statusClass(ok)}">${ok ? "ok" : "fail"}</span></td>
          <td>${fmt(syncState.latest_block_height, 0)}</td>
          <td class="hash" title="${escapeHtml(syncState.latest_block_hash)}">${escapeHtml(shortHash(syncState.latest_block_hash))}</td>
          <td>${fmt(syncState.pending_replay_blocks, 0)}</td>
          <td>${peerCount(syncState)}</td>
          <td>${escapeHtml(formatDate(syncState.checked_at))}</td>
        </tr>
      `;
    })
    .join("");
}

function renderBlocks() {
  const blocks = [...state.blocks]
    .sort((a, b) => Number(b.height || 0) - Number(a.height || 0))
    .slice(0, 25);
  if (!blocks.length) {
    $("blocksTable").innerHTML = `<tr><td colspan="7" class="empty">Waiting for blocks</td></tr>`;
    return;
  }
  $("blocksTable").innerHTML = blocks
    .map(
      (block) => `
        <tr>
          <td>${fmt(block.height, 0)}</td>
          <td class="hash" title="${escapeHtml(block.block_hash)}">${escapeHtml(shortHash(block.block_hash))}</td>
          <td class="hash" title="${escapeHtml(block.previous_hash)}">${escapeHtml(shortHash(block.previous_hash))}</td>
          <td class="mono">${escapeHtml(block.miner_id)}</td>
          <td>${fmt(block.range_start, 0)}..${fmt(block.range_end, 0)}</td>
          <td>${fmt(block.reward, 5)}</td>
          <td>${escapeHtml(formatDate(block.timestamp))}</td>
        </tr>
      `
    )
    .join("");
}

function renderTransactions() {
  const txs = [...(state.transactions || [])].slice(0, 40);
  if (!txs.length) {
    $("transactionsTable").innerHTML = `<tr><td colspan="8" class="empty">No recent transactions</td></tr>`;
    return;
  }
  $("transactionsTable").innerHTML = txs
    .map(
      (tx) => `
        <tr>
          <td><span class="status-pill ${tx.status === "confirmed" ? "ok" : tx.status === "rejected" ? "bad" : "warn"}">${escapeHtml(tx.status)}</span></td>
          <td class="hash" title="${escapeHtml(tx.tx_hash)}">${escapeHtml(shortHash(tx.tx_hash))}</td>
          <td>${escapeHtml(tx.tx_type)}</td>
          <td>${fmt(tx.amount, 5)}</td>
          <td>${fmt(tx.fee, 5)}</td>
          <td class="hash" title="${escapeHtml(tx.sender)}">${escapeHtml(shortHash(tx.sender))}</td>
          <td class="hash" title="${escapeHtml(tx.recipient)}">${escapeHtml(shortHash(tx.recipient))}</td>
          <td>${fmt(tx.block_height, 0)}</td>
        </tr>
      `
    )
    .join("");
}

function renderValidators() {
  if (!state.validators.length) {
    $("validatorsGrid").innerHTML = `<div class="empty">Waiting for validators</div>`;
    return;
  }
  const validators = [...state.validators].sort((a, b) => Number(b.selection_score || 0) - Number(a.selection_score || 0));
  $("validatorsGrid").innerHTML = validators
    .slice(0, 100)
    .map((validator) => {
      const status = validator.is_banned ? "Banned" : Number(validator.stake_locked || 0) > 0 ? "Eligible" : "Inactive";
      return `
        <article class="validator-card">
          <header>
            <strong class="mono">${escapeHtml(validator.validator_id)}</strong>
            <span>${status}</span>
          </header>
          <dl>
            <div><dt>Score</dt><dd>${fmt(validator.selection_score, 4)}</dd></div>
            <div><dt>Trust</dt><dd>${fmt(validator.trust_score, 4)}</dd></div>
            <div><dt>Stake</dt><dd>${fmt(validator.stake_locked, 5)}</dd></div>
            <div><dt>Rewards</dt><dd>${fmt(validator.total_rewards, 5)}</dd></div>
          </dl>
        </article>
      `;
    })
    .join("");
}

function renderEvents() {
  if (!state.events.length) {
    $("eventsList").innerHTML = `<div class="empty">Waiting for events</div>`;
    return;
  }
  $("eventsList").innerHTML = state.events
    .map(
      (event) => `
        <article class="event-row">
          <header>
            <strong>${escapeHtml(event.title)}</strong>
            <span>${escapeHtml(formatDate(event.created_at))}</span>
          </header>
          <p>${escapeHtml(event.message)}</p>
        </article>
      `
    )
    .join("");
}

function renderErrors() {
  if (!state.errors.length && state.nodeStates.every((node) => !node.error)) {
    $("apiErrors").innerHTML = "";
    return;
  }
  const nodeErrors = state.nodeStates
    .filter((node) => node.error)
    .map((node) => ({ path: node.url, message: node.error }));
  $("apiErrors").innerHTML = [...state.errors, ...nodeErrors]
    .map(
      (error) => `
        <div class="api-error">
          <strong>${escapeHtml(error.path)}</strong>: ${escapeHtml(error.message)}
        </div>
      `
    )
    .join("");
}

async function runLookup() {
  const input = $("lookupInput");
  const value = input.value.trim();
  const result = $("lookupResult");
  if (!value) {
    result.innerHTML = "";
    return;
  }
  result.innerHTML = `<div class="empty">Searching...</div>`;
  try {
    if (/^\d+$/.test(value)) {
      const block = await fetchJson(`/blocks/${value}`);
      result.innerHTML = `
        <article class="lookup-card">
          <span>Block ${fmt(block.height, 0)}</span>
          <strong class="hash" title="${escapeHtml(block.block_hash)}">${escapeHtml(block.block_hash)}</strong>
          <p>Miner ${escapeHtml(block.miner_id)} - ${fmt(block.tx_count, 0)} tx - ${escapeHtml(formatDate(block.timestamp))}</p>
        </article>
      `;
      return;
    }
    if (/^[a-f0-9]{64}$/i.test(value)) {
      const tx = await fetchJson(`/tx/${value}`);
      result.innerHTML = `
        <article class="lookup-card">
          <span>${escapeHtml(tx.status)} transaction</span>
          <strong class="hash">${escapeHtml(tx.tx_hash)}</strong>
          <p>${escapeHtml(tx.sender)} -> ${escapeHtml(tx.recipient || "-")} - ${fmt(tx.amount, 5)} PI - fee ${fmt(tx.fee, 5)}</p>
        </article>
      `;
      return;
    }
    result.innerHTML = `<div class="api-error">Enter a block height or 64-character transaction hash.</div>`;
  } catch (error) {
    result.innerHTML = `<div class="api-error">${escapeHtml(error.message)}</div>`;
  }
}

$("lookupButton")?.addEventListener("click", runLookup);
$("lookupInput")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") runLookup();
});

loadExplorer();
window.setInterval(loadExplorer, refreshMs);
