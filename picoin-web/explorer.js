const explorerConfig = window.PICOIN_EXPLORER_CONFIG || {};
const apiBaseUrl = (explorerConfig.apiBaseUrl || "").replace(/\/$/, "");

const state = {
  health: null,
  stats: null,
  blocks: [],
  validators: [],
  events: [],
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

async function loadExplorer() {
  state.errors = [];
  await Promise.all([
    loadEndpoint("health", "/health", null),
    loadEndpoint("stats", "/stats", null),
    loadEndpoint("blocks", "/blocks", []),
    loadEndpoint("validators", "/validators?limit=100", []),
    loadEndpoint("events", "/events?limit=16", []),
  ]);
  render();
}

function render() {
  renderStatus();
  renderBlocks();
  renderValidators();
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
  $("metricReserve").textContent = fmt(state.stats?.total_science_reserve_rewards, 5);
  $("metricTreasury").textContent = fmt(state.stats?.total_scientific_development_rewards, 5);
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

loadExplorer();
window.setInterval(loadExplorer, 30000);
