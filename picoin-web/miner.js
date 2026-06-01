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
  const badge = $("minerStatusBadge");
  badge.textContent = status;
  badge.className = `status-pill ${statusClass(status)}`;
  $("minerTitle").textContent = result.type === "reward_wallet" ? `Reward wallet ${result.query}` : `Miner ${result.query}`;
  $("minerSummary").innerHTML = `
    ${summaryCard("Status", status, `${fmt(result.online_miners, 0)} online miner(s)`)}
    ${summaryCard("Accepted Blocks", fmt(summary.accepted_blocks, 0), `first ${summary.first_block_height || "-"}`)}
    ${summaryCard("Total Rewards", `${fmt(summary.total_rewards, 5)} PI`, `balance ${fmt(account.balance, 5)} PI`)}
    ${summaryCard("Avg Rate", fmtRate(summary.avg_work_rate_hps), `task ${fmtMs(summary.avg_total_task_ms)}`)}
    ${summaryCard("Avg Difficulty", fmt(summary.avg_difficulty, 4), `latest block ${summary.latest_block_height || "-"}`)}
    ${summaryCard("Last Activity", formatDate(summary.latest_block_at), `checked ${formatDate(result.checked_at)}`)}
  `;
}

function renderMiners(miners) {
  const table = $("minerTable");
  if (!miners.length) {
    table.innerHTML = `<tr><td colspan="8" class="empty">No registered miner matched this query</td></tr>`;
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
          <td>${fmt(miner.total_rewards, 5)} PI</td>
          <td>${fmt(miner.balance, 5)} PI</td>
          <td class="hash" title="${escapeHtml(miner.last_task_id)}">${escapeHtml(shortHash(miner.last_task_id))}<div class="muted">${escapeHtml(miner.last_task_status || "-")}</div></td>
          <td>${escapeHtml(formatDate(miner.last_seen_at || miner.last_heartbeat_at))}</td>
        </tr>
      `
    )
    .join("");
}

function renderBlocks(blocks) {
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
          <td>${fmt(block.reward, 5)} PI</td>
          <td>${escapeHtml(formatDate(block.timestamp))}</td>
        </tr>
      `
    )
    .join("");
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
  const result = await fetchJson(`/miners/lookup/${encodeURIComponent(value)}?limit=50`);
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
  renderBlocks(result.recent_blocks || []);
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
