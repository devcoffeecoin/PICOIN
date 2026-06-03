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

function fmt(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });
}

function shortHash(value) {
  if (!value) return "-";
  const text = String(value);
  if (text.length <= 18) return text;
  return `${text.slice(0, 10)}...${text.slice(-8)}`;
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

function currentBlockHeight(context = {}) {
  const candidates = [
    context.current_height,
    context.latest_block_height,
    context.accepted_blocks,
  ]
    .map((value) => Number(value || 0))
    .filter((value) => value > 0);
  return candidates.length ? Math.max(...candidates) : 0;
}

function blockMaturityDepth(context = {}) {
  return Math.max(0, Number(context.block_maturity_depth || 0));
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

function statusClass(status) {
  if (status === "online") return "ok";
  if (status === "stale" || status === "known") return "warn";
  return "bad";
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

function asArray(value, keys = []) {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== "object") return [];
  for (const key of keys) {
    if (Array.isArray(value[key])) return value[key];
  }
  return [];
}

async function fetchOptional(path, fallback) {
  try {
    return await fetchJson(path);
  } catch {
    return fallback;
  }
}

function readQueryFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return (params.get("q") || params.get("miner") || params.get("wallet") || "").trim();
}

function updateUrl(query) {
  const url = new URL(window.location.href);
  url.searchParams.set("q", query);
  window.history.replaceState({}, "", url.toString());
}

function renderError(message) {
  $("minerError").innerHTML = `<div class="api-error">${escapeHtml(message)}</div>`;
  const badge = $("minerStatusBadge");
  badge.textContent = "Error";
  badge.className = "status-pill bad";
}

function clearError() {
  $("minerError").innerHTML = "";
}

function summaryCard(label, value, detail = "") {
  return `
    <article>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      ${detail ? `<small>${escapeHtml(detail)}</small>` : ""}
    </article>
  `;
}

function renderSummary(result) {
  const summary = result.summary || {};
  const account = result.account || {};
  const status = result.status || "unknown";
  const isRewardWallet = result.type === "reward_wallet";
  const latestBlock = asArray(result.recent_blocks, ["blocks", "items", "results"])[0] || null;
  const badge = $("minerStatusBadge");
  badge.textContent = status;
  badge.className = `status-pill ${statusClass(status)}`;
  $("minerTitle").textContent = isRewardWallet ? `Reward wallet ${result.query}` : `Miner ${result.query}`;
  $("minerSummary").innerHTML = `
    ${summaryCard("Status", status, `${fmt(result.online_miners, 0)} online miner(s)`)}
    ${summaryCard("Blocks Won", fmt(summary.accepted_blocks, 0), `first ${summary.first_block_height || "-"}`)}
    ${
      isRewardWallet
        ? summaryCard("Reward Total", `${fmt(summary.total_rewards, 5)} PI`, `wallet balance ${fmt(account.balance, 5)} PI`)
        : summaryCard("Latest Maturity", maturityText(latestBlock, summary), `depth ${fmt(blockMaturityDepth(summary), 0)} blocks`)
    }
    ${summaryCard("Avg Rate", fmtRate(summary.avg_work_rate_hps), `compute ${fmtMs(summary.avg_compute_ms ?? summary.avg_total_task_ms)}`)}
    ${summaryCard("Avg Difficulty", fmt(summary.avg_difficulty, 4), `latest block ${summary.latest_block_height || "-"}`)}
    ${summaryCard("Last Activity", formatDate(summary.latest_block_at), `checked ${formatDate(result.checked_at)}`)}
  `;
}

function renderMiners(miners) {
  const table = $("minerTable");
  if (!miners.length) {
    table.innerHTML = `<tr><td colspan="6" class="empty">No registered miner matched this query</td></tr>`;
    return;
  }
  table.innerHTML = miners
    .map(
      (miner) => `
        <tr>
          <td class="mono">${escapeHtml(miner.miner_id)}</td>
          <td><span class="status-pill ${statusClass(miner.online_status)}">${escapeHtml(miner.online_status)}</span></td>
          <td class="hash" title="${escapeHtml(miner.reward_address)}">${escapeHtml(shortHash(miner.reward_address))}</td>
          <td>${fmt(miner.accepted_blocks, 0)}</td>
          <td class="hash" title="${escapeHtml(miner.last_task_id)}">${escapeHtml(shortHash(miner.last_task_id))}<div class="muted">${escapeHtml(miner.last_task_status || "-")}</div></td>
          <td>${escapeHtml(formatDate(miner.last_seen_at || miner.last_heartbeat_at))}</td>
        </tr>
      `
    )
    .join("");
}

function renderBlocks(blocks, context = {}) {
  const table = $("minerBlocksTable");
  if (!blocks.length) {
    table.innerHTML = `<tr><td colspan="9" class="empty">No accepted blocks for this search</td></tr>`;
    return;
  }
  table.innerHTML = blocks
    .map(
      (block) => `
        <tr>
          <td>${fmt(block.height, 0)}</td>
          <td class="hash" title="${escapeHtml(block.block_hash)}">${escapeHtml(shortHash(block.block_hash))}</td>
          <td class="hash" title="${escapeHtml(block.result_hash)}">${escapeHtml(shortHash(block.result_hash))}</td>
          <td class="mono">${escapeHtml(block.miner_id)}</td>
          <td>${fmt(block.range_start, 0)}..${fmt(block.range_end, 0)}</td>
          <td>${fmt(block.difficulty, 4)}</td>
          <td>${escapeHtml(fmtRate(block.work_rate_hps || block.hashrate_hps))}</td>
          <td>${maturityBadge(block, context)}</td>
          <td>${escapeHtml(formatDate(block.timestamp))}</td>
        </tr>
      `
    )
    .join("");
}

async function fetchMinerLookup(value) {
  try {
    return await fetchJson(`/miners/lookup/${encodeURIComponent(value)}?limit=50`);
  } catch (error) {
    if (!/not found|404/i.test(error.message)) throw error;
    return fallbackMinerLookup(value);
  }
}

async function fallbackMinerLookup(value) {
  const query = String(value || "").trim();
  const normalized = query.toUpperCase();
  const [minersPayload, blocksPayload, protocol, sync, account] = await Promise.all([
    fetchOptional("/miners/status?limit=1000", { miners: [] }),
    fetchOptional("/blocks", []),
    fetchOptional("/protocol", {}),
    fetchOptional("/node/sync-status", {}),
    normalized.startsWith("PI") ? fetchOptional(`/accounts/${encodeURIComponent(normalized)}`, null) : Promise.resolve(null),
  ]);
  const miners = asArray(minersPayload, ["miners", "items", "results"]).filter((miner) => {
    return miner.miner_id === query || String(miner.reward_address || "").toUpperCase() === normalized;
  });
  const minerIds = new Set(miners.map((miner) => miner.miner_id));
  const recentBlocks = asArray(blocksPayload, ["blocks", "items", "results"])
    .filter((block) => {
      return (
        block.miner_id === query ||
        minerIds.has(block.miner_id) ||
        String(block.miner_reward_address || "").toUpperCase() === normalized
      );
    })
    .sort((a, b) => Number(b.height || 0) - Number(a.height || 0))
    .slice(0, 50)
    .map((block) => {
      const rangeStart = Number(block.range_start || 0);
      const rangeEnd = Number(block.range_end || rangeStart);
      const segmentSize = Math.max(1, rangeEnd - rangeStart + 1);
      const computeMs = Number(block.compute_ms || block.total_task_ms || block.total_block_ms || 0);
      const taskMs = Number(block.total_task_ms || computeMs || block.total_block_ms || 0);
      const workRate = computeMs > 0 ? segmentSize / (computeMs / 1000) : 0;
      return {
        ...block,
        segment_size: segmentSize,
        compute_ms: computeMs,
        total_task_ms: taskMs,
        work_rate_hps: workRate,
        hashrate_hps: workRate,
      };
    });
  const totalRewards = recentBlocks.reduce((total, block) => total + Number(block.reward || 0), 0);
  const avgTask = recentBlocks.length
    ? recentBlocks.reduce((total, block) => total + Number(block.total_task_ms || 0), 0) / recentBlocks.length
    : 0;
  const avgDifficulty = recentBlocks.length
    ? recentBlocks.reduce((total, block) => total + Number(block.difficulty || 0), 0) / recentBlocks.length
    : 0;
  const latest = recentBlocks[0];
  const first = recentBlocks[recentBlocks.length - 1];
  const currentHeight = Math.max(
    Number(sync.effective_latest_block_height || sync.latest_block_height || 0),
    ...recentBlocks.map((block) => Number(block.height || 0))
  );
  const maturityDepth = Number(protocol.block_maturity_depth || 0);
  const onlineMiners = miners.filter((miner) => miner.online_status === "online").length;
  const segmentSize = Number(recentBlocks[0]?.segment_size || 64);
  const liveRates = miners
    .filter((miner) => miner.online_status === "online")
    .map((miner) => minerComputeRate(miner, segmentSize))
    .filter((rate) => rate > 0);
  const liveRate = liveRates.reduce((total, rate) => total + rate, 0);
  return {
    query,
    found: Boolean(miners.length || recentBlocks.length || account),
    type: normalized.startsWith("PI") ? "reward_wallet" : "miner",
    status: onlineMiners ? "online" : miners.length || account ? "known" : "not_found",
    online_miners: onlineMiners,
    miner_count: miners.length,
    miners,
    account,
    summary: {
      accepted_blocks: recentBlocks.length,
      total_rewards: totalRewards,
      avg_total_task_ms: avgTask,
      avg_total_block_ms: avgTask,
      avg_difficulty: avgDifficulty,
      avg_work_rate_hps: liveRate || (avgTask > 0 ? segmentSize / (avgTask / 1000) : 0),
      first_block_height: first?.height || null,
      latest_block_height: latest?.height || null,
      latest_block_at: latest?.timestamp || null,
      block_maturity_depth: maturityDepth,
      current_height: currentHeight,
    },
    recent_blocks: recentBlocks,
    checked_at: new Date().toISOString(),
  };
}

async function searchMiner(query) {
  const value = String(query || "").trim();
  if (!value) {
    renderError("Enter a miner ID or PI reward wallet.");
    return;
  }
  clearError();
  const badge = $("minerStatusBadge");
  badge.textContent = "Loading";
  badge.className = "status-pill warn";
  $("minerLookupInput").value = value;
  updateUrl(value);
  const result = await fetchMinerLookup(value);
  if (!result.found) {
    badge.textContent = "Not found";
    badge.className = "status-pill bad";
    $("minerTitle").textContent = "No miner found";
    $("minerSummary").innerHTML = `<div class="empty">No miner, reward wallet or accepted block matched this query.</div>`;
    renderMiners([]);
    renderBlocks([]);
    return;
  }
  renderSummary(result);
  renderMiners(result.miners || []);
  renderBlocks(result.recent_blocks || [], result.summary || {});
}

$("minerLookupButton")?.addEventListener("click", () => {
  searchMiner($("minerLookupInput").value).catch((error) => renderError(error.message));
});

$("minerLookupInput")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    searchMiner($("minerLookupInput").value).catch((error) => renderError(error.message));
  }
});

const initialQuery = readQueryFromUrl();
if (initialQuery) {
  searchMiner(initialQuery).catch((error) => renderError(error.message));
}
