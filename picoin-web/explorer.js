const explorerConfig = window.PICOIN_EXPLORER_CONFIG || {};

function cleanUrl(value) {
  return String(value || "").replace(/\/$/, "");
}

const configuredNodes = Array.isArray(explorerConfig.nodes)
  ? explorerConfig.nodes.filter((node) => node && node.enabled !== false)
  : [];
const fallbackApiBaseUrl = cleanUrl(explorerConfig.apiBaseUrl || window.location.origin);
const nodes = (configuredNodes.length ? configuredNodes : [{ label: "Primary", url: fallbackApiBaseUrl }]).map((node, index) => ({
  label: node.label || `Node ${index + 1}`,
  url: cleanUrl(node.url || fallbackApiBaseUrl),
}));
const apiBaseUrl = cleanUrl(explorerConfig.apiBaseUrl || nodes[0]?.url || fallbackApiBaseUrl);
const refreshMs = Number(explorerConfig.refreshMs || 30000);
const transactionLimit = 20;
const defaultFetchTimeoutMs = 12000;
const apiClient = window.PicoinApiFailover
  ? window.PicoinApiFailover.createClient({
      config: explorerConfig,
      defaultBaseUrl: apiBaseUrl,
      storageKey: `picoin-explorer-active-bootstrap:${apiBaseUrl}`,
      timeoutMs: defaultFetchTimeoutMs,
    })
  : null;
const defaultEndpointTtlMs = 10000;
const endpointTtls = {
  health: 5000,
  stats: 20000,
  sync: 5000,
  audit: 300000,
  consensus: 15000,
  protocol: 60000,
  difficultyStatus: 15000,
  miningMetrics: 15000,
  reserve: 60000,
  treasury: 60000,
  validatorsStatus: 15000,
  minersStatus: 15000,
  mempoolStatus: 10000,
  blocks: 15000,
  retroAudits: 300000,
  validators: 60000,
  events: 30000,
  transactions: 15000,
};

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
  endpointLoadedAt: {},
};
let loadExplorerRunning = false;

const $ = (id) => document.getElementById(id);
const persistentEndpointKeys = Object.keys(endpointTtls);
const storagePrefix = `picoin-explorer:${apiBaseUrl}:`;

function storageAvailable() {
  try {
    return typeof window.localStorage !== "undefined";
  } catch (_error) {
    return false;
  }
}

function storageKey(key) {
  return `${storagePrefix}${key}`;
}

function restoreEndpointFromStorage(key) {
  if (!storageAvailable()) return false;
  try {
    const raw = window.localStorage.getItem(storageKey(key));
    if (!raw) return false;
    const cached = JSON.parse(raw);
    if (!cached || !cached.storedAt) return false;
    state[key] = cached.value;
    state.endpointLoadedAt[key] = Number(cached.storedAt) || Date.now();
    return true;
  } catch (_error) {
    return false;
  }
}

function persistEndpointToStorage(key) {
  if (!storageAvailable()) return;
  try {
    window.localStorage.setItem(
      storageKey(key),
      JSON.stringify({
        storedAt: state.endpointLoadedAt[key] || Date.now(),
        value: state[key],
      })
    );
  } catch (_error) {
    // Browser storage is best-effort; the explorer can run without it.
  }
}

function hydrateStoredEndpoints() {
  persistentEndpointKeys.forEach(restoreEndpointFromStorage);
}

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

function minerStatusMap() {
  return new Map(
    asArray(state.minersStatus?.miners, ["miners", "items", "results"])
      .filter((miner) => miner?.miner_id)
      .map((miner) => [miner.miner_id, miner])
  );
}

function minerDisplayName(minerLike) {
  const minerId = typeof minerLike === "string" ? minerLike : minerLike?.miner_id;
  if (!minerId) return "-";
  const liveMiner = minerStatusMap().get(minerId);
  const name = String(minerLike?.name || minerLike?.miner_name || liveMiner?.name || "").trim();
  if (!name || name === minerId) return minerId;
  return `${name}:${minerId}`;
}

function linkedMinerDisplay(minerLike) {
  const minerId = typeof minerLike === "string" ? minerLike : minerLike?.miner_id;
  if (!minerId) return "-";
  return linkedMiner(minerId, minerDisplayName(minerLike));
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

function minerComputeRate(miner, segmentSize = 64) {
  const computeMs = Number(miner?.last_compute_ms ?? miner?.avg_compute_ms ?? 0);
  return computeMs > 0 ? Number(segmentSize || 64) / (computeMs / 1000) : 0;
}

function activeNetworkCompute(segmentSize = 64) {
  const miners = asArray(state.minersStatus?.miners, ["miners", "items", "results"]);
  const onlineRates = miners
    .filter((miner) => miner.online_status === "online")
    .map((miner) => minerComputeRate(miner, segmentSize))
    .filter((rate) => rate > 0);
  return {
    online_compute_miners: onlineRates.length,
    network_compute_rate_hps: onlineRates.reduce((total, rate) => total + rate, 0),
  };
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

function healthAcceptable(health) {
  if (health?.status === "ok") return true;
  const issues = Array.isArray(health?.issues) ? health.issues : [];
  return (
    health?.status === "degraded" &&
    issues.length > 0 &&
    issues.every((issue) => /not enough eligible validators for quorum/i.test(String(issue)))
  );
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

function currentBlockHeight(context = {}) {
  const candidates = [
    context.current_height,
    state.miningMetrics?.summary?.current_height,
    effectiveHeight(state.sync),
    state.audit?.rewards?.accepted_blocks,
    state.stats?.accepted_blocks,
  ]
    .map((value) => Number(value || 0))
    .filter((value) => value > 0);
  return candidates.length ? Math.max(...candidates) : 0;
}

function blockMaturityDepth(context = {}) {
  const value =
    context.block_maturity_depth ??
    state.miningMetrics?.summary?.block_maturity_depth ??
    state.audit?.rewards?.block_maturity_depth ??
    state.protocol?.block_maturity_depth ??
    0;
  return Math.max(0, Number(value || 0));
}

function rewardMaturity(block, context = {}) {
  if (!block) {
    return { label: "-", detail: "", className: "warn", title: "No accepted block yet" };
  }
  const status = String(block.reward_status || "").toLowerCase();
  const height = Number(block.height || 0);
  const depth = blockMaturityDepth(context);
  const currentHeight = currentBlockHeight(context);
  let maturesAt = Number(block.matures_at_height || 0);
  if (!maturesAt && height > 0 && depth > 0) {
    maturesAt = height + depth;
  }
  const remaining = maturesAt > 0 ? Math.max(0, maturesAt - currentHeight) : 0;
  const isMature =
    status === "mature" ||
    (status !== "immature" && depth === 0) ||
    (maturesAt > 0 && currentHeight > 0 && remaining === 0);

  if (isMature) {
    return {
      label: "Mature",
      detail: maturesAt > 0 ? `at #${fmt(maturesAt, 0)}` : "confirmed",
      className: "ok",
      title: "Reward is spendable",
    };
  }

  if (status === "immature" || maturesAt > 0) {
    return {
      label: "Immature",
      detail: remaining > 0 ? `${fmt(remaining, 0)} block${remaining === 1 ? "" : "s"}` : `at #${fmt(maturesAt, 0)}`,
      className: "warn",
      title: maturesAt > 0 ? `Matures at block ${fmt(maturesAt, 0)}` : "Waiting for reward maturity",
    };
  }

  return {
    label: "Pending",
    detail: depth > 0 ? `${fmt(depth, 0)} block maturity` : "",
    className: "warn",
    title: "Reward maturity status is not available yet",
  };
}

function maturityBadge(block, context = {}) {
  const info = rewardMaturity(block, context);
  if (info.label === "-") return "-";
  return `
    <span class="status-pill ${info.className}" title="${escapeHtml(info.title)}">${escapeHtml(info.label)}</span>
    ${info.detail ? `<div class="muted">${escapeHtml(info.detail)}</div>` : ""}
  `;
}

function maturityText(block, context = {}) {
  const info = rewardMaturity(block, context);
  return info.detail ? `${info.label} (${info.detail})` : info.label;
}

function endpointFresh(key, ttlMs = defaultEndpointTtlMs) {
  const loadedAt = Number(state.endpointLoadedAt[key] || 0);
  return loadedAt > 0 && Date.now() - loadedAt < ttlMs;
}

function rememberEndpoint(key) {
  state.endpointLoadedAt[key] = Date.now();
  persistEndpointToStorage(key);
}

function rememberError(path, error, stale = false) {
  state.errors.push({ path, message: error?.message || String(error), stale });
}

async function fetchJsonFrom(baseUrl, path, options = {}) {
  const timeoutMs = Number(options.timeoutMs || defaultFetchTimeoutMs);
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const safePath = path.startsWith('/') ? path : `/${path}`;
    const url = `${cleanUrl(baseUrl)}${safePath}`;
    const response = await fetch(url, { 
      headers: { Accept: "application/json" },
      mode: 'cors',
      signal: controller.signal,
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
    if (error.name === "AbortError") {
      throw new Error(`Timeout after ${fmt(timeoutMs / 1000, 0)}s at ${baseUrl}${path}`);
    }
    if (error.name === 'TypeError') {
      throw new Error(`CORS Blocked or Network Down at ${baseUrl}${path}`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function fetchJson(path, options = {}) {
  if (apiClient) {
    const result = await apiClient.fetchJson(path, options);
    return result.payload;
  }
  return fetchJsonFrom(apiBaseUrl, path, options);
}

async function loadEndpoint(key, path, fallback, options = {}) {
  const ttlMs = Number(options.ttlMs ?? endpointTtls[key] ?? defaultEndpointTtlMs);
  if (endpointFresh(key, ttlMs)) return;
  try {
    state[key] = await fetchJson(path, options);
    rememberEndpoint(key);
  } catch (error) {
    if (state.endpointLoadedAt[key]) {
      rememberError(path, error, true);
      return;
    }
    state[key] = fallback;
    rememberError(path, error);
  }
}

async function loadMiningMetrics() {
  if (endpointFresh("miningMetrics", endpointTtls.miningMetrics)) return;
  try {
    state.miningMetrics = await fetchJson("/mining/metrics?limit=120");
    rememberEndpoint("miningMetrics");
  } catch (error) {
    if (!/not found|404/i.test(error.message)) {
      if (state.endpointLoadedAt.miningMetrics) {
        rememberError("/mining/metrics?limit=120", error, true);
      } else {
        state.miningMetrics = null;
        rememberError("/mining/metrics?limit=120", error);
      }
    }
  }
}

async function loadNodeState(node) {
  const previous = state.nodeStates.find((item) => item.url === node.url);
  if (cleanUrl(node.url) === apiBaseUrl && state.health && state.sync) {
    return { ...node, health: state.health, sync: state.sync, ok: healthAcceptable(state.health), error: null };
  }
  try {
    const [health, sync] = await Promise.all([
      fetchJsonFrom(node.url, "/health", { timeoutMs: 8000 }),
      fetchJsonFrom(node.url, "/node/sync-status", { timeoutMs: 8000 }),
    ]);
    return { ...node, health, sync, ok: healthAcceptable(health), error: null };
  } catch (error) {
    if (previous?.sync) {
      return { ...previous, ok: false, stale: true, error: error.message };
    }
    return { ...node, health: null, sync: null, ok: false, error: error.message };
  }
}

async function fetchTransactions() {
  if (endpointFresh("transactions", endpointTtls.transactions)) return state.transactions;
  const endpoints = [
    `/transactions/recent?limit=${transactionLimit}`,
    `/mempool?status=pending&limit=${transactionLimit}`,
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
    const message = errors.map((error) => `${error.path}: ${error.message}`).join("; ");
    if (state.endpointLoadedAt.transactions) {
      rememberError("transactions", new Error(message), true);
      return state.transactions;
    }
    rememberError("transactions", new Error(message));
  }
  state.transactions = transactions;
  rememberEndpoint("transactions");
  return transactions;
}

async function runLimited(tasks, limit = 4) {
  const queue = [...tasks];
  const workers = Array.from({ length: Math.min(limit, queue.length) }, async () => {
    while (queue.length) {
      const task = queue.shift();
      await task();
    }
  });
  await Promise.all(workers);
}

async function loadExplorer() {
  if (loadExplorerRunning) return;
  loadExplorerRunning = true;
  state.errors = [];
  try {
    const coreTasks = [
      () => loadEndpoint("health", "/health", null),
      () => loadEndpoint("sync", "/node/sync-status", null),
      () => loadEndpoint("protocol", "/protocol", null),
      () => loadEndpoint("validatorsStatus", "/validators/status", null),
      () => loadEndpoint("minersStatus", "/miners/status", null, { allowFailover: false }),
      () => loadEndpoint("blocks", "/blocks?limit=50", []),
      async () => {
        try {
          state.transactions = await fetchTransactions();
        } catch (error) {
          if (state.endpointLoadedAt.transactions) {
            rememberError("transactions", error, true);
          } else {
            state.transactions = [];
            rememberError("transactions", error);
          }
        }
      },
    ];
    await runLimited(coreTasks, 3);
    state.nodeStates = await Promise.all(nodes.map(loadNodeState));
    render();

    const secondaryTasks = [
      () => loadEndpoint("stats", "/stats", null),
      () => loadEndpoint("consensus", "/consensus/status", null),
      () => loadEndpoint("difficultyStatus", "/difficulty", null),
      () => loadMiningMetrics(),
      () => loadEndpoint("reserve", "/reserve/status", null),
      () => loadEndpoint("treasury", "/treasury/status", null),
      () => loadEndpoint("mempoolStatus", "/mempool/status", null),
      () => loadEndpoint("validators", "/validators?limit=100", []),
      () => loadEndpoint("events", "/events?limit=16", []),
    ];
    await runLimited(secondaryTasks, 3);
    render();

    const slowTasks = [
      () => loadEndpoint("audit", "/audit/full", null),
      () => loadEndpoint("retroAudits", "/audit/retroactive?limit=100", []),
    ];
    await runLimited(slowTasks, 1);
    render();
  } finally {
    loadExplorerRunning = false;
  }
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
  if (hashRateEl) {
    const miningSummary = (state.miningMetrics || deriveMiningMetrics()).summary || {};
    hashRateEl.textContent = fmtRate(miningSummary.network_compute_rate_hps ?? miningSummary.avg_work_rate_hps);
  }
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
  const metrics = state.miningMetrics || deriveMiningMetrics();
  const summary = metrics.summary || {};
  const networkRate = summary.network_compute_rate_hps ?? summary.avg_work_rate_hps;
  const blockComputeRate = summary.avg_accepted_block_work_rate_hps ?? summary.avg_work_rate_hps;
  const rateSource = summary.network_compute_rate_source || "miner_heartbeat";
  const rateDetail =
    rateSource === "accepted_block_estimate"
      ? `${fmt(summary.active_miners, 0)} active miners est.`
      : `${fmt(summary.online_compute_miners ?? summary.active_miners, 0)} live samples`;
  const blocks = asArray(metrics.blocks, ["blocks", "items", "results"]);
  const summaryEl = $("miningChartSummary");
  if (summaryEl) {
    summaryEl.innerHTML = `
      <article>
        <span>Network Rate</span>
        <strong>${escapeHtml(fmtRate(networkRate))}</strong>
        <small>${escapeHtml(rateDetail)}</small>
      </article>
      <article>
        <span>Block Compute</span>
        <strong>${escapeHtml(fmtRate(blockComputeRate))}</strong>
        <small>accepted blocks</small>
      </article>
      <article>
        <span>Avg Finalized</span>
        <strong>${escapeHtml(fmtMs(summary.avg_total_block_ms))}</strong>
        <small>compute + queue</small>
      </article>
      <article>
        <span>Difficulty</span>
        <strong>${fmt(summary.latest_difficulty ?? state.difficultyStatus?.active_difficulty ?? state.protocol?.difficulty, 4)}</strong>
        <small>${fmt(summary.blocks_sampled, 0)} blocks</small>
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
  const latestBlockByMiner = new Map();
  for (const block of blocks) {
    const minerId = block.miner_id;
    if (!minerId) continue;
    const previous = latestBlockByMiner.get(minerId);
    if (!previous || Number(block.height || 0) >= Number(previous.height || 0)) {
      latestBlockByMiner.set(minerId, block);
    }
  }
  if (!miners.length) {
    table.innerHTML = `<tr><td colspan="7" class="empty">Waiting for miner data</td></tr>`;
    return;
  }
  table.innerHTML = miners
    .map((miner) => {
      const latestMinerBlock = latestBlockByMiner.get(miner.miner_id);
      return `
        <tr>
          <td class="mono">${linkedMinerDisplay(miner)}</td>
          <td class="hash">${linkedMiner(miner.miner_reward_address, miner.miner_reward_address ? shortHash(miner.miner_reward_address) : "-")}</td>
          <td>${fmt(miner.accepted_blocks, 0)}</td>
          <td>${maturityBadge(latestMinerBlock)}</td>
          <td>${escapeHtml(fmtRate(miner.avg_work_rate_hps))}</td>
          <td>${escapeHtml(fmtMs(miner.avg_total_task_ms))}</td>
          <td>${fmt(miner.latest_block_height, 0)}</td>
        </tr>
      `;
    })
    .join("");
}

function deriveMiningMetrics() {
  const blocks = asArray(state.blocks, ["blocks", "items", "results"])
    .sort((a, b) => Number(a.height || 0) - Number(b.height || 0))
    .slice(-120)
    .map((block) => {
      const rangeStart = Number(block.range_start || 0);
      const rangeEnd = Number(block.range_end || rangeStart);
      const segmentSize = Math.max(1, rangeEnd - rangeStart + 1);
      const computeMs = Number(block.compute_ms || block.total_task_ms || block.total_block_ms || 0);
      const taskMs = Number(block.total_task_ms || computeMs || block.total_block_ms || 0);
      const blockMs = Number(block.total_block_ms || block.total_task_ms || 0);
      const workRate = computeMs > 0 ? segmentSize / (computeMs / 1000) : 0;
      return {
        ...block,
        segment_size: segmentSize,
        compute_ms: computeMs,
        total_task_ms: taskMs,
        total_block_ms: blockMs,
        validation_ms: Number(block.validation_ms || 0),
        work_rate_hps: workRate,
        hashrate_hps: workRate,
        block_rate_hps: blockMs > 0 ? segmentSize / (blockMs / 1000) : 0,
      };
    });
  const workRates = blocks.map((block) => Number(block.work_rate_hps || 0)).filter((value) => value > 0);
  const blockTimes = blocks.map((block) => Number(block.total_block_ms || 0)).filter((value) => value > 0);
  const latest = blocks[blocks.length - 1];
  const segmentSize = Number(state.protocol?.segment_size || latest?.segment_size || 64);
  const networkCompute = activeNetworkCompute(segmentSize);
  const minerGroups = new Map();
  for (const block of blocks) {
    const minerId = block.miner_id || "-";
    const existing = minerGroups.get(minerId) || {
      miner_id: minerId,
      miner_reward_address: block.miner_reward_address || null,
      accepted_blocks: 0,
      compute_ms: 0,
      total_task_ms: 0,
      avg_difficulty: 0,
      latest_block_height: 0,
      latest_block_at: null,
    };
    existing.accepted_blocks += 1;
    existing.compute_ms += Number(block.compute_ms || block.total_task_ms || 0);
    existing.total_task_ms += Number(block.total_task_ms || 0);
    existing.avg_difficulty += Number(block.difficulty || 0);
    if (Number(block.height || 0) >= existing.latest_block_height) {
      existing.latest_block_height = Number(block.height || 0);
      existing.latest_block_at = block.timestamp;
      existing.miner_reward_address = block.miner_reward_address || existing.miner_reward_address;
    }
    minerGroups.set(minerId, existing);
  }
  const minerStatusById = new Map(
    asArray(state.minersStatus?.miners, ["miners", "items", "results"])
      .filter((miner) => miner.miner_id)
      .map((miner) => [miner.miner_id, miner])
  );
  const topMiners = Array.from(minerGroups.values())
    .map((miner) => {
      const liveMiner = minerStatusById.get(miner.miner_id);
      const liveRate = minerComputeRate(liveMiner, segmentSize);
      const avgCompute = miner.accepted_blocks ? miner.compute_ms / miner.accepted_blocks : 0;
      const avgTask = miner.accepted_blocks ? miner.total_task_ms / miner.accepted_blocks : 0;
      return {
        ...miner,
        name: liveMiner?.name || miner.name || null,
        online_status: liveMiner?.online_status,
        avg_compute_ms: Number(liveMiner?.last_compute_ms || avgCompute || 0),
        avg_total_task_ms: avgTask,
        avg_difficulty: miner.accepted_blocks ? miner.avg_difficulty / miner.accepted_blocks : 0,
        avg_work_rate_hps: liveRate || (avgCompute > 0 ? segmentSize / (avgCompute / 1000) : 0),
      };
    })
    .sort((a, b) => Number(b.accepted_blocks || 0) - Number(a.accepted_blocks || 0))
    .slice(0, 12);
  const statusMiners = asArray(state.minersStatus?.miners, ["miners", "items", "results"])
    .map((miner) => ({
      miner_id: miner.miner_id,
      name: miner.name || null,
      miner_reward_address: miner.reward_address,
      online_status: miner.online_status,
      accepted_blocks: Number(miner.accepted_blocks || 0),
      avg_compute_ms: Number(miner.last_compute_ms || 0),
      avg_total_task_ms: Number(miner.last_compute_ms || 0),
      avg_difficulty: latest?.difficulty ?? state.difficultyStatus?.active_difficulty ?? state.protocol?.difficulty,
      avg_work_rate_hps: minerComputeRate(miner, segmentSize),
      latest_block_height: Number(miner.last_block_height || 0),
      latest_block_at: miner.last_seen_at || miner.last_heartbeat_at || null,
    }))
    .filter((miner) => miner.miner_id && (miner.avg_work_rate_hps > 0 || miner.online_status === "online"))
    .sort((a, b) => Number(b.avg_work_rate_hps || 0) - Number(a.avg_work_rate_hps || 0))
    .slice(0, 12);
  const avgBlockComputeRate = workRates.length ? workRates.reduce((a, b) => a + b, 0) / workRates.length : 0;
  const activeMiners = Number(state.minersStatus?.counts?.online || 0);
  const networkRate =
    networkCompute.network_compute_rate_hps ||
    (avgBlockComputeRate > 0 && activeMiners > 0 ? avgBlockComputeRate * activeMiners : 0);
  return {
    summary: {
      current_height: latest?.height || 0,
      latest_block_hash: latest?.block_hash || "",
      latest_difficulty: latest?.difficulty ?? state.difficultyStatus?.active_difficulty ?? state.protocol?.difficulty,
      active_difficulty: state.difficultyStatus?.active_difficulty ?? state.protocol?.difficulty,
      network_compute_rate_hps: networkRate,
      avg_work_rate_hps: networkRate || avgBlockComputeRate,
      avg_accepted_block_work_rate_hps: avgBlockComputeRate,
      avg_total_block_ms: blockTimes.length ? blockTimes.reduce((a, b) => a + b, 0) / blockTimes.length : 0,
      blocks_sampled: blocks.length,
      online_compute_miners: networkCompute.online_compute_miners,
      network_compute_rate_source: networkCompute.network_compute_rate_hps ? "miner_heartbeat" : (networkRate ? "accepted_block_estimate" : "none"),
      active_miners: activeMiners,
    },
    blocks,
    top_miners: topMiners.length ? topMiners : statusMiners,
  };
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
    <svg class="mining-chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Accepted block compute rate and difficulty">
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
        <text x="${pad.left}" y="18">Block compute max ${escapeHtml(fmtRate(maxRate))}</text>
        <text x="${pad.left + innerWidth}" y="18" text-anchor="end">Difficulty max ${fmt(maxDifficulty, 4)}</text>
        <text x="${pad.left + innerWidth}" y="${pad.top + innerHeight - 8}" text-anchor="end">Latest ${escapeHtml(fmtRate(latestRate))}</text>
      </g>
    </svg>
    <div class="chart-legend">
      <span><i class="legend-rate"></i>Block compute rate</span>
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
          <td class="mono">${linkedMinerDisplay(block)}</td>
          <td>${fmt(block.range_start, 0)}..${fmt(block.range_end, 0)}</td>
          <td>${maturityBadge(block)}</td>
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
    .map((node) => ({ path: node.url, message: node.error, stale: Boolean(node.stale) }));
  const uniqueErrors = [];
  const seen = new Set();
  for (const error of [...state.errors, ...nodeErrors]) {
    const message = String(error.message || "");
    const normalizedMessage = message.replace(/https?:\/\/[^/\s]+/g, "{host}");
    const key = `${error.path}|${normalizedMessage}|${Boolean(error.stale)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    uniqueErrors.push(error);
  }

  const staleCount = uniqueErrors.filter((error) => error.stale).length;
  const hardErrors = uniqueErrors.filter((error) => !error.stale).slice(0, 5);
  const moreCount = Math.max(0, uniqueErrors.length - staleCount - hardErrors.length);
  const staleNotice =
    staleCount > 0
      ? `
        <div class="api-error">
          <strong>Partial delay</strong>: ${fmt(staleCount, 0)} endpoint${staleCount === 1 ? "" : "s"} using last known data while the bootstrap responds slowly.
        </div>
      `
      : "";
  const hardNotice = hardErrors
    .map(
      (error) => `
        <div class="api-error">
          <strong>${escapeHtml(error.path)}</strong>: ${escapeHtml(error.message)}
        </div>
      `
    )
    .join("");
  const moreNotice =
    moreCount > 0
      ? `<div class="api-error"><strong>More</strong>: ${fmt(moreCount, 0)} additional temporary API error${moreCount === 1 ? "" : "s"} hidden.</div>`
      : "";
  errEl.innerHTML = `${staleNotice}${hardNotice}${moreNotice}`;
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
          <p>Miner ${escapeHtml(minerDisplayName(block))} - maturity ${escapeHtml(maturityText(block))} - ${fmt(block.tx_count, 0)} tx - ${escapeHtml(formatDate(block.timestamp))}</p>
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

hydrateStoredEndpoints();
render();
loadExplorer();
window.setInterval(loadExplorer, refreshMs);
