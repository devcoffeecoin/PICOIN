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
const transactionLimit = 20;

const state = {
  health: null,
  stats: null,
  sync: null,
  audit: null,
  consensus: null,
  protocol: null,
  difficultyStatus: null,
  miningMetrics: null,
  reserve: null,
  treasury: null,
  validatorsStatus: null,
  minersStatus: null,
  mempoolStatus: null,
  blocks: [],
  retroAudits: [],
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

function txDetailHref(hash) {
  return hash ? `./transaction.html?hash=${encodeURIComponent(hash)}` : "./transaction.html";
}

function linkedTx(hash, label = null) {
  if (!hash) return "-";
  return `<a class="hash-link" href="${txDetailHref(hash)}" title="${escapeHtml(hash)}">${escapeHtml(label || shortHash(hash))}</a>`;
}

function minerDetailHref(query) {
  return query ? `./miner.html?q=${encodeURIComponent(query)}` : "./miner.html";
}

function linkedMiner(query, label = null) {
  if (!query) return "-";
  return `<a class="hash-link" href="${minerDetailHref(query)}" title="${escapeHtml(query)}">${escapeHtml(label || shortHash(query))}</a>`;
}

function txId(tx) {
  return tx?.tx_hash || tx?.hash || tx?.id || "";
}

function txTimestamp(tx) {
  const value = tx?.confirmed_at || tx?.selected_at || tx?.created_at || tx?.timestamp || tx?.received_at || tx?.inserted_at || "";
  const time = value ? new Date(value).getTime() : 0;
  return Number.isNaN(time) ? 0 : time;
}

function txStatusClass(status) {
  if (status === "confirmed") return "ok";
  if (["rejected", "failed", "expired"].includes(status)) return "bad";
  return "warn";
}

function normalizeTransaction(tx) {
  const hash = txId(tx);
  return {
    ...tx,
    tx_hash: hash,
    status: tx.status || "unknown",
    tx_type: tx.tx_type || tx.type || "-",
  };
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

function fmtMs(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const ms = Number(value);
  if (ms >= 1000) return `${fmt(ms / 1000, 2)} s`;
  return `${fmt(ms, 0)} ms`;
}

function fmtRate(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const rate = Number(value);
  if (rate >= 1000) return `${fmt(rate / 1000, 2)} kH/s`;
  return `${fmt(rate, 2)} H/s`;
}

function asArray(value, keys = []) {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== "object") return [];
  for (const key of keys) {
    if (Array.isArray(value[key])) return value[key];
  }
  return [];
}

function statusClass(ok) {
  return ok ? "ok" : "bad";
}

function peerCount(sync) {
  const counts = sync?.peer_counts || {};
  return `${fmt(counts.connected, 0)} / ${fmt(counts.total, 0)}`;
}

function effectiveHeight(sync) {
  const latestHeight = Number(sync?.effective_latest_block_height ?? sync?.latest_block_height ?? 0);
  const snapshotHeight = Number(sync?.active_snapshot_base?.height ?? 0);
  return Math.max(latestHeight, snapshotHeight);
}

function effectiveHash(sync) {
  if (sync?.effective_latest_block_hash) return sync.effective_latest_block_hash;
  const latestHeight = Number(sync?.latest_block_height ?? 0);
  const snapshotHeight = Number(sync?.active_snapshot_base?.height ?? 0);
  if (snapshotHeight > latestHeight && sync?.active_snapshot_base?.block_hash) {
    return sync.active_snapshot_base.block_hash;
  }
  return sync?.latest_block_hash || "";
}

function blockBaseReward(block) {
  const protocolReward = Number(state.protocol?.reward_per_block || 0);
  if (protocolReward > 0) return protocolReward;

  const minerReward = Number(block?.reward || 0);
  const minerPercent = Number(state.protocol?.proof_of_pi_reward_percent || 0);
  if (minerReward > 0 && minerPercent > 0) return minerReward / minerPercent;

  return minerReward;
}

function formatBlockReward(block) {
  return fmt(blockBaseReward(block), 5);
}

async function fetchJsonFrom(baseUrl, path) {
  try {
    const safePath = path.startsWith('/') ? path : `/${path}`;
    const url = `${cleanUrl(baseUrl)}${safePath}`;
    const response = await fetch(url, { 
      headers: { Accept: "application/json" },
      mode: 'cors'
    });
    let payload = {};
    const contentType = response.headers.get("content-type");
    if (contentType && contentType.includes("application/json")) {
      payload = await response.json().catch(() => ({}));
    }
    if (!response.ok) {
      throw new Error(payload.detail || response.statusText || `Error ${response.status} en ${url}`);
    }
    return payload;
  } catch (error) {
    if (error.name === 'TypeError') {
      throw new Error(`CORS Blocked or Network Down at ${baseUrl}${path}`);
    }
    throw error;
  }
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

async function fetchTransactions() {
  const endpoints = [
    `/transactions/recent?limit=${transactionLimit}`,
    `/mempool?limit=${transactionLimit}`,
    `/mempool?status=pending&limit=${transactionLimit}`,
    `/mempool?status=selected&limit=${transactionLimit}`,
    `/mempool?status=confirmed&limit=${transactionLimit}`,
    `/mempool?status=released&limit=${transactionLimit}`,
  ];
  let allTxs = [];
  const errors = [];
  
  for (const path of endpoints) {
    try {
      const data = await fetchJson(path);
      const extracted = asArray(data, ["transactions", "items", "results", "mempool"]);
      if (extracted.length > 0) {
        allTxs = allTxs.concat(extracted);
      }
    } catch (error) {
      errors.push({ path, message: error.message });
      console.warn(`Explorer: Skip ${path} - ${error.message}`);
    }
  }

  const seen = new Set();
  const transactions = allTxs
    .map(normalizeTransaction)
    .filter((tx) => {
      const id = txId(tx);
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    })
    .sort((a, b) => txTimestamp(b) - txTimestamp(a))
    .slice(0, transactionLimit);

  if (!transactions.length && errors.length) {
    state.errors.push({ path: "transactions", message: errors.map((error) => `${error.path}: ${error.message}`).join("; ") });
  }
  return transactions;
}

async function loadExplorer() {
  state.errors = [];
  await Promise.all([
    loadEndpoint("health", "/health", null),
    loadEndpoint("stats", "/stats", null),
    loadEndpoint("sync", "/node/sync-status", null),
    loadEndpoint("audit", "/audit/full", null),
    loadEndpoint("consensus", "/consensus/status", null),
    loadEndpoint("protocol", "/protocol", null),
    loadEndpoint("difficultyStatus", "/difficulty", null),
    loadEndpoint("miningMetrics", "/mining/metrics?limit=120", null),
    loadEndpoint("reserve", "/reserve/status", null),
    loadEndpoint("treasury", "/treasury/status", null),
    loadEndpoint("validatorsStatus", "/validators/status", null),
    loadEndpoint("minersStatus", "/miners/status", null),
    loadEndpoint("mempoolStatus", "/mempool/status", null),
    loadEndpoint("blocks", "/blocks", []),
    loadEndpoint("retroAudits", "/audit/retroactive?limit=100", []),
    loadEndpoint("validators", "/validators?limit=100", []),
    loadEndpoint("events", "/events?limit=16", []),
    (async () => {
      try {
        state.transactions = await fetchTransactions();
      } catch (error) {
        state.transactions = [];
        state.errors.push({ path: "transactions", message: error.message });
      }
    })(),
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
      effectiveHeight(sync) === effectiveHeight(first.sync) &&
      effectiveHash(sync) === effectiveHash(first.sync) &&
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
  renderMining();
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
  if (apiStatus) {
    apiStatus.textContent = ready ? "Network Ready" : partial ? "Degraded" : "Offline";
    apiStatus.className = `status-pill ${ready ? "ok" : partial ? "warn" : "bad"}`;
  }

  const setMetric = (id, val, digits = 0) => {
    const el = $(id);
    if (el) el.textContent = fmt(val, digits);
  };

  setMetric("metricHeight", effectiveHeight(state.sync) || state.health?.latest_block_height, 0);
  
  const chainEl = $("metricChain");
  if (chainEl) chainEl.textContent = state.sync?.network_id || state.health?.network_id || "-";
  
  setMetric("metricSupply", state.audit?.supply?.actual_total_balances ?? state.stats?.circulating_supply, 5);
  setMetric("metricValidators", state.validatorsStatus?.counts?.total ?? state.health?.database?.validators, 0);
  setMetric("metricActiveMiners", state.minersStatus?.counts?.online ?? state.health?.database?.miners, 0);
  const hashRateEl = $("metricHashRate");
  if (hashRateEl) hashRateEl.textContent = fmtRate(state.miningMetrics?.summary?.avg_work_rate_hps);
  setMetric("metricDifficulty", state.miningMetrics?.summary?.latest_difficulty ?? state.difficultyStatus?.active_difficulty ?? state.protocol?.difficulty, 4);
  setMetric("metricActiveValidators", state.validatorsStatus?.counts?.online, 0);
  setMetric("metricEligibleValidators", state.validatorsStatus?.eligible_validators ?? state.health?.database?.eligible_validators, 0);
  
  const resEl = $("metricReserve");
  if (resEl) resEl.textContent = state.reserve?.status || fmt(state.stats?.total_science_reserve_rewards, 5);
  
  setMetric("metricReplay", state.sync?.pending_replay_blocks, 0);
}

function renderNetwork() {
  const ready = explorerReady();
  const sync = state.sync || {};
  const tipHeight = effectiveHeight(sync);
  const tipHash = effectiveHash(sync);
  const auditOk = state.audit?.valid === true;
  const nodeOkCount = state.nodeStates.filter((node) => node.ok && node.sync).length;
  const summary = $("networkSummary");
  if (summary) summary.innerHTML = `
    <article>
      <span>Consensus View</span>
      <strong class="${ready ? "good-text" : "warn-text"}">${ready ? "Aligned" : "Needs attention"}</strong>
    </article>
    <article>
      <span>Tip</span>
      <strong title="${escapeHtml(tipHash)}">${fmt(tipHeight, 0)} / ${escapeHtml(shortHash(tipHash))}</strong>
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
      <strong>${fmt(state.mempoolStatus?.pending_count ?? asArray(state.transactions, ["transactions", "items", "results"]).filter((tx) => tx.status === "pending").length, 0)}</strong>
    </article>
    <article>
      <span>Fork Groups</span>
      <strong>${fmt(state.consensus?.fork_group_count, 0)}</strong>
    </article>
  `;

  const table = $("nodesTable");
  if (!table) return;
  if (!state.nodeStates.length) {
    table.innerHTML = `<tr><td colspan="7" class="empty">Waiting for peers</td></tr>`;
    return;
  }
  table.innerHTML = state.nodeStates
    .map((node) => {
      const syncState = node.sync || {};
      const ok = node.ok && Number(syncState.pending_replay_blocks || 0) === 0;
      const nodeHeight = effectiveHeight(syncState);
      const nodeHash = effectiveHash(syncState);
      return `
        <tr>
          <td>
            <strong>${escapeHtml(node.label)}</strong>
            <div class="muted mono">${escapeHtml(node.url)}</div>
          </td>
          <td><span class="status-pill ${statusClass(ok)}">${ok ? "ok" : "fail"}</span></td>
          <td>${fmt(nodeHeight, 0)}</td>
          <td class="hash" title="${escapeHtml(nodeHash)}">${escapeHtml(shortHash(nodeHash))}</td>
          <td>${fmt(syncState.pending_replay_blocks, 0)}</td>
          <td>${peerCount(syncState)}</td>
          <td>${escapeHtml(formatDate(syncState.checked_at))}</td>
        </tr>
      `;
    })
    .join("");
}

function renderMining() {
  const metrics = state.miningMetrics || {};
  const summary = metrics.summary || {};
  const blocks = asArray(metrics.blocks, ["blocks", "items", "results"]);
  const summaryEl = $("miningChartSummary");
  if (summaryEl) {
    summaryEl.innerHTML = `
      <article>
        <span>Avg Rate</span>
        <strong>${escapeHtml(fmtRate(summary.avg_work_rate_hps))}</strong>
      </article>
      <article>
        <span>Difficulty</span>
        <strong>${fmt(summary.latest_difficulty ?? state.difficultyStatus?.active_difficulty ?? state.protocol?.difficulty, 4)}</strong>
      </article>
      <article>
        <span>Avg Block</span>
        <strong>${escapeHtml(fmtMs(summary.avg_total_block_ms))}</strong>
      </article>
      <article>
        <span>Sample</span>
        <strong>${fmt(summary.blocks_sampled, 0)} blocks</strong>
      </article>
    `;
  }

  const chart = $("miningChart");
  if (chart) {
    chart.innerHTML = renderMiningChart(blocks);
  }

  const table = $("minerLeaderboard");
  if (!table) return;
  const miners = asArray(metrics.top_miners, ["miners", "items", "results"]);
  if (!miners.length) {
    table.innerHTML = `<tr><td colspan="7" class="empty">Waiting for miner data</td></tr>`;
    return;
  }
  table.innerHTML = miners
    .map(
      (miner) => `
        <tr>
          <td class="mono">${linkedMiner(miner.miner_id, miner.miner_id)}</td>
          <td class="hash">${linkedMiner(miner.miner_reward_address, miner.miner_reward_address ? shortHash(miner.miner_reward_address) : "-")}</td>
          <td>${fmt(miner.accepted_blocks, 0)}</td>
          <td>${fmt(miner.total_rewards, 5)} PI</td>
          <td>${escapeHtml(fmtRate(miner.avg_work_rate_hps))}</td>
          <td>${escapeHtml(fmtMs(miner.avg_total_task_ms))}</td>
          <td>${fmt(miner.latest_block_height, 0)}</td>
        </tr>
      `
    )
    .join("");
}

function renderMiningChart(blocks) {
  const rows = blocks.filter((block) => Number(block.height) > 0);
  if (!rows.length) {
    return `<div class="empty">Waiting for accepted blocks</div>`;
  }
  const width = 960;
  const height = 320;
  const pad = { top: 28, right: 62, bottom: 48, left: 58 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const xFor = (index) => pad.left + (rows.length === 1 ? innerWidth / 2 : (index / (rows.length - 1)) * innerWidth);
  const rateValues = rows.map((block) => Number(block.work_rate_hps || block.hashrate_hps || 0));
  const difficultyValues = rows.map((block) => Number(block.difficulty || 0));
  const maxRate = Math.max(...rateValues, 1);
  const maxDifficulty = Math.max(...difficultyValues, 1);
  const yRate = (value) => pad.top + innerHeight - (Number(value || 0) / maxRate) * innerHeight;
  const yDifficulty = (value) => pad.top + innerHeight - (Number(value || 0) / maxDifficulty) * innerHeight;
  const ratePoints = rows.map((block, index) => `${xFor(index)},${yRate(block.work_rate_hps || block.hashrate_hps)}`).join(" ");
  const difficultyPoints = rows.map((block, index) => `${xFor(index)},${yDifficulty(block.difficulty)}`).join(" ");
  const bars = rows
    .map((block, index) => {
      const x = xFor(index);
      const y = yRate(block.work_rate_hps || block.hashrate_hps);
      const barWidth = Math.max(3, Math.min(12, innerWidth / Math.max(rows.length, 1) - 3));
      return `<rect x="${x - barWidth / 2}" y="${y}" width="${barWidth}" height="${pad.top + innerHeight - y}" rx="2" />`;
    })
    .join("");
  const first = rows[0];
  const last = rows[rows.length - 1];
  const latestRate = last.work_rate_hps || last.hashrate_hps;
  return `
    <svg class="mining-chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Accepted block work rate and difficulty">
      <defs>
        <linearGradient id="rateFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#21c7ff" stop-opacity="0.42" />
          <stop offset="100%" stop-color="#21c7ff" stop-opacity="0.05" />
        </linearGradient>
      </defs>
      <g class="chart-grid">
        <line x1="${pad.left}" y1="${pad.top}" x2="${pad.left}" y2="${pad.top + innerHeight}" />
        <line x1="${pad.left}" y1="${pad.top + innerHeight}" x2="${pad.left + innerWidth}" y2="${pad.top + innerHeight}" />
        <line x1="${pad.left}" y1="${pad.top + innerHeight * 0.66}" x2="${pad.left + innerWidth}" y2="${pad.top + innerHeight * 0.66}" />
        <line x1="${pad.left}" y1="${pad.top + innerHeight * 0.33}" x2="${pad.left + innerWidth}" y2="${pad.top + innerHeight * 0.33}" />
      </g>
      <g class="rate-bars">${bars}</g>
      <polyline class="rate-line" points="${ratePoints}" />
      <polyline class="difficulty-line" points="${difficultyPoints}" />
      <g class="chart-labels">
        <text x="${pad.left}" y="${height - 16}">#${fmt(first.height, 0)}</text>
        <text x="${pad.left + innerWidth}" y="${height - 16}" text-anchor="end">#${fmt(last.height, 0)}</text>
        <text x="${pad.left}" y="18">Rate max ${escapeHtml(fmtRate(maxRate))}</text>
        <text x="${pad.left + innerWidth}" y="18" text-anchor="end">Difficulty max ${fmt(maxDifficulty, 4)}</text>
        <text x="${pad.left + innerWidth}" y="${pad.top + innerHeight - 8}" text-anchor="end">Latest ${escapeHtml(fmtRate(latestRate))}</text>
      </g>
    </svg>
    <div class="chart-legend">
      <span><i class="legend-rate"></i>Work rate</span>
      <span><i class="legend-difficulty"></i>Difficulty</span>
    </div>
  `;
}

function renderBlocks() {
  const blocks = asArray(state.blocks, ["blocks", "items", "results"])
    .sort((a, b) => Number(b.height || 0) - Number(a.height || 0))
    .slice(0, 25);
  const table = $("blocksTable");
  if (!table) return;
  if (!blocks.length) {
    table.innerHTML = `<tr><td colspan="7" class="empty">Waiting for blocks</td></tr>`;
    return;
  }
  table.innerHTML = blocks
    .map(
      (block) => `
        <tr>
          <td>${fmt(block.height, 0)}</td>
          <td class="hash" title="${escapeHtml(block.block_hash)}">${escapeHtml(shortHash(block.block_hash))}</td>
          <td class="hash" title="${escapeHtml(block.previous_hash)}">${escapeHtml(shortHash(block.previous_hash))}</td>
          <td class="mono">${linkedMiner(block.miner_id, block.miner_id)}</td>
          <td>${fmt(block.range_start, 0)}..${fmt(block.range_end, 0)}</td>
          <td title="Miner share: ${escapeHtml(fmt(block.reward, 5))}">${escapeHtml(formatBlockReward(block))}</td>
          <td>${escapeHtml(formatDate(block.timestamp))}</td>
        </tr>
      `
    )
    .join("");
}

function renderTransactions() {
  let txs = asArray(state.transactions, ["transactions", "items", "results", "mempool"]);
  txs = txs
    .map(normalizeTransaction)
    .sort((a, b) => txTimestamp(b) - txTimestamp(a))
    .slice(0, transactionLimit);
  const table = $("transactionsTable");
  if (!table) return;
  if (!txs.length) {
    table.innerHTML = `<tr><td colspan="8" class="empty">No recent transactions</td></tr>`;
    return;
  }
  table.innerHTML = txs
    .map(
      (tx) => `
        <tr>
          <td><span class="status-pill ${txStatusClass(tx.status)}">${escapeHtml(tx.status)}</span></td>
          <td class="hash">${linkedTx(tx.tx_hash)}</td>
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
  const grid = $("validatorsGrid");
  if (!grid) return;
  const validatorCounts = state.validatorsStatus?.counts || {};
  const minerCounts = state.minersStatus?.counts || {};
  if (!state.validatorsStatus && !state.minersStatus) {
    grid.innerHTML = `<div class="empty">Waiting for validators</div>`;
    return;
  }
  const required = state.validatorsStatus?.required_validator_approvals ?? state.consensus?.required_validator_approvals;
  grid.innerHTML = `
    <article class="validator-card summary-card">
      <header><strong>Miners</strong><span>live</span></header>
      <dl>
        <div><dt>Active</dt><dd>${fmt(minerCounts.online, 0)}</dd></div>
        <div><dt>Stale</dt><dd>${fmt(minerCounts.stale, 0)}</dd></div>
        <div><dt>Offline</dt><dd>${fmt(minerCounts.offline, 0)}</dd></div>
        <div><dt>Total</dt><dd>${fmt(minerCounts.total, 0)}</dd></div>
      </dl>
    </article>
    <article class="validator-card summary-card">
      <header><strong>Validators</strong><span>live</span></header>
      <dl>
        <div><dt>Active</dt><dd>${fmt(validatorCounts.online, 0)}</dd></div>
        <div><dt>Eligible</dt><dd>${fmt(state.validatorsStatus?.eligible_validators, 0)}</dd></div>
        <div><dt>Stale</dt><dd>${fmt(validatorCounts.stale, 0)}</dd></div>
        <div><dt>Offline</dt><dd>${fmt(validatorCounts.offline, 0)}</dd></div>
      </dl>
    </article>
    <article class="validator-card summary-card">
      <header><strong>Quorum</strong><span>${state.validatorsStatus?.eligible_validators >= required ? "ready" : "waiting"}</span></header>
      <dl>
        <div><dt>Required</dt><dd>${fmt(required, 0)}</dd></div>
        <div><dt>Out of sync</dt><dd>${fmt(validatorCounts.out_of_sync, 0)}</dd></div>
        <div><dt>Disabled</dt><dd>${fmt(validatorCounts.disabled, 0)}</dd></div>
        <div><dt>Total</dt><dd>${fmt(validatorCounts.total, 0)}</dd></div>
      </dl>
    </article>
  `;
}

function renderEvents() {
  const list = $("eventsList");
  if (!list) return;
  const events = asArray(state.events, ["events", "items", "results"]);
  if (!events.length) {
    list.innerHTML = `<div class="empty">Waiting for events</div>`;
    return;
  }
  list.innerHTML = events
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
  const errEl = $("apiErrors");
  if (!errEl) return;
  if (!state.errors.length && state.nodeStates.every((node) => !node.error)) {
    errEl.innerHTML = "";
    return;
  }
  const nodeErrors = state.nodeStates
    .filter((node) => node.error)
    .map((node) => ({ path: node.url, message: node.error }));
  errEl.innerHTML = [...state.errors, ...nodeErrors]
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
          <p>Miner ${escapeHtml(block.miner_id)} - reward ${escapeHtml(formatBlockReward(block))} - ${fmt(block.tx_count, 0)} tx - ${escapeHtml(formatDate(block.timestamp))}</p>
        </article>
      `;
      return;
    }
    if (/^[a-f0-9]{64}$/i.test(value)) {
      const tx = await fetchJson(`/tx/${value}`);
      result.innerHTML = `
        <article class="lookup-card">
          <span>${escapeHtml(tx.status)} transaction</span>
          <strong class="hash">${linkedTx(tx.tx_hash, tx.tx_hash)}</strong>
          <p>${escapeHtml(tx.sender)} -> ${escapeHtml(tx.recipient || "-")} - ${fmt(tx.amount, 5)} PI - fee ${fmt(tx.fee, 5)}</p>
        </article>
      `;
      return;
    }
    if (/^miner_[a-z0-9]+$/i.test(value) || /^PI[A-Z0-9]{20,}$/i.test(value)) {
      window.location.href = minerDetailHref(value);
      return;
    }
    result.innerHTML = `<div class="api-error">Enter a block height, transaction hash, miner ID or PI reward wallet.</div>`;
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
