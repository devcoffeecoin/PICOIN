const config = window.PICOIN_CONFIG || {};
const apiBaseUrl = (config.apiBaseUrl || "").replace(/\/$/, "");

const state = {
  health: null,
  stats: null,
  blocks: [],
  events: [],
  reserve: null,
  treasury: null,
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

async function fetchJson(path) {
  const response = await fetch(`${apiBaseUrl}${path}`, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || response.statusText || `HTTP ${response.status}`);
  }
  return payload;
}

async function loadEndpoint(key, path, fallback) {
  try {
    state[key] = await fetchJson(path);
  } catch (error) {
    state[key] = fallback;
    state.errors.push({ path, message: error.message });
  }
}

async function loadData() {
  state.errors = [];
  await Promise.all([
    loadEndpoint("health", "/health", null),
    loadEndpoint("stats", "/stats", null),
    loadEndpoint("blocks", "/blocks", []),
    loadEndpoint("events", "/events?limit=8", []),
    loadEndpoint("reserve", "/reserve/status", null),
    loadEndpoint("treasury", "/treasury/status", null),
  ]);
  render();
}

function render() {
  renderStatus();
  renderBlocks();
  renderEvents();
  renderErrors();
}

function renderStatus() {
  const healthy = state.health && state.health.status === "ok";
  const apiStatus = $("apiStatus");
  apiStatus.textContent = healthy ? "Live" : state.errors.length ? "Partial" : "Offline";
  apiStatus.className = `status-pill ${healthy ? "ok" : state.errors.length ? "warn" : "bad"}`;

  $("metricHeight").textContent = fmt(state.health?.latest_block_height, 0);
  $("metricChain").textContent = state.health?.chain?.valid ? "Valid" : "-";
  $("metricSupply").textContent = fmt(state.stats?.circulating_supply, 5);
  $("metricValidators").textContent = fmt(state.health?.database?.eligible_validators, 0);
  $("metricReserve").textContent = fmt(state.stats?.total_science_reserve_rewards ?? state.reserve?.available, 5);
  $("metricTreasury").textContent = fmt(state.stats?.total_scientific_development_rewards ?? state.treasury?.locked_balance, 5);
}

function renderBlocks() {
  const blocks = [...state.blocks]
    .sort((a, b) => Number(b.height || 0) - Number(a.height || 0))
    .slice(0, 8);
  if (!blocks.length) {
    $("blocksTable").innerHTML = `<tr><td colspan="5" class="empty">Waiting for blocks</td></tr>`;
    return;
  }
  $("blocksTable").innerHTML = blocks
    .map(
      (block) => `
        <tr>
          <td>${fmt(block.height, 0)}</td>
          <td class="hash" title="${escapeHtml(block.block_hash)}">${escapeHtml(shortHash(block.block_hash))}</td>
          <td class="mono">${escapeHtml(block.miner_id)}</td>
          <td>${fmt(block.reward, 5)}</td>
          <td>${fmt(block.range_start, 0)}..${fmt(block.range_end, 0)}</td>
        </tr>
      `
    )
    .join("");
}

function renderEvents() {
  if (!state.events.length) {
    $("eventsList").innerHTML = `<div class="empty">Waiting for events</div>`;
    return;
  }
  $("eventsList").innerHTML = state.events
    .slice(0, 8)
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
  if (!state.errors.length) {
    $("apiErrors").innerHTML = "";
    return;
  }
  $("apiErrors").innerHTML = state.errors
    .map(
      (error) => `
        <div class="api-error">
          <strong>${escapeHtml(error.path)}</strong>: ${escapeHtml(error.message)}
        </div>
      `
    )
    .join("");
}

loadData();
window.setInterval(loadData, 30000);
