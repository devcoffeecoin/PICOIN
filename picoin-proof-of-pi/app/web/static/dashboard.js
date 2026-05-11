const state = {
  protocol: null,
  stats: null,
  performance: null,
  difficulty: null,
  preview: null,
  blocks: [],
  validators: [],
  audit: null,
  chain: null,
  health: null,
  node: null,
  events: [],
};

const $ = (id) => document.getElementById(id);

function text(id, value) {
  $(id).textContent = value;
}

function fmt(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("en-US", {
    maximumFractionDigits: digits,
  });
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

async function fetchJson(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail || response.statusText;
    throw new Error(detail);
  }
  return payload;
}

async function loadData() {
  const [
    protocol,
    stats,
    performance,
    difficulty,
    preview,
    blocks,
    validators,
    audit,
    chain,
    health,
    node,
    events,
  ] = await Promise.all([
    fetchJson("/protocol"),
    fetchJson("/stats"),
    fetchJson("/stats/performance"),
    fetchJson("/difficulty"),
    fetchJson("/difficulty/preview"),
    fetchJson("/blocks"),
    fetchJson("/validators?limit=100"),
    fetchJson("/audit/full"),
    fetchJson("/blocks/verify"),
    fetchJson("/health"),
    fetchJson("/node/status"),
    fetchJson("/events?limit=14"),
  ]);

  Object.assign(state, {
    protocol,
    stats,
    performance,
    difficulty,
    preview,
    blocks,
    validators,
    audit,
    chain,
    health,
    node,
    events,
  });
  render();
}

function render() {
  renderMetrics();
  renderNode();
  renderEvents();
  renderBlocks();
  renderValidators();
  renderDifficulty();
  renderPerformance();
  renderAudit();
}

function renderMetrics() {
  text("networkBadge", `${state.protocol.network_id} - v${state.protocol.protocol_version}`);
  text("nodeStatusBadge", state.health.status);
  $("nodeStatusBadge").className = `status-pill ${state.health.status === "ok" ? "ok" : "warn"}`;
  text("metricBlocks", fmt(state.stats.accepted_blocks, 0));
  text("metricDifficulty", fmt(state.protocol.difficulty, 3));
  text("metricMinted", fmt(state.stats.total_minted_rewards, 5));
  text("metricAvgTime", `${fmt(state.performance.avg_total_task_ms, 0)} ms`);
  text("metricValidators", fmt(state.validators.length, 0));
  text("metricAudit", state.audit.valid ? "OK" : "Issues");
  $("metricAudit").style.color = state.audit.valid ? "var(--green)" : "var(--red)";
  text("faucetStatus", state.protocol.faucet_enabled ? "habilitado" : "bloqueado");
  $("faucetStatus").className = `status-pill ${state.protocol.faucet_enabled ? "ok" : "bad"}`;
}

function renderNode() {
  text("nodeHealth", state.health.mining_ready ? "mining ready" : state.health.status);
  $("nodeHealth").className = `status-pill ${state.health.mining_ready ? "ok" : "warn"}`;
  const counts = state.node.counts;
  $("nodeSummary").innerHTML = `
    <div class="summary-box"><span>Uptime</span><strong>${fmt(state.node.uptime_seconds, 0)} s</strong></div>
    <div class="summary-box"><span>Altura</span><strong>${fmt(state.node.latest_block_height, 0)}</strong></div>
    <div class="summary-box"><span>Validadores elegibles</span><strong>${fmt(counts.eligible_validators, 0)} / ${fmt(counts.required_validator_approvals, 0)}</strong></div>
    <div class="summary-box"><span>Jobs pendientes</span><strong>${fmt(counts.validation_jobs.pending, 0)}</strong></div>
    <div class="summary-box"><span>Asignacion</span><strong>${state.health.can_assign_tasks ? "activa" : "bloqueada"}</strong></div>
    <div class="summary-box"><span>Cadena</span><strong>${state.node.chain_valid ? "valida" : "revisar"}</strong></div>
  `;
}

function renderEvents() {
  text("eventsCount", `${state.events.length}`);
  if (!state.events.length) {
    $("eventsList").innerHTML = `<div class="empty">Sin eventos recientes</div>`;
    return;
  }
  $("eventsList").innerHTML = state.events
    .map(
      (event) => `
        <article class="event-row ${event.severity}">
          <header>
            <strong>${escapeHtml(event.title)}</strong>
            <span>${escapeHtml(new Date(event.created_at).toLocaleTimeString())}</span>
          </header>
          <p>${escapeHtml(event.message)}</p>
        </article>
      `
    )
    .join("");
}

function renderBlocks() {
  const tbody = $("blocksTable");
  const recent = [...state.blocks].reverse().slice(0, 20);
  if (!recent.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">Sin bloques aceptados</td></tr>`;
  } else {
    tbody.innerHTML = recent
      .map(
        (block) => `
          <tr>
            <td>${block.height}</td>
            <td class="mono">${escapeHtml(block.miner_id)}</td>
            <td>${block.range_start}..${block.range_end}</td>
            <td>${fmt(block.reward, 5)}</td>
            <td>${fmt(block.difficulty, 3)}</td>
            <td class="hash" title="${escapeHtml(block.block_hash)}">${escapeHtml(shortHash(block.block_hash))}</td>
          </tr>
        `
      )
      .join("");
  }
  text("chainStatus", state.chain.valid ? "cadena valida" : "revisar cadena");
  $("chainStatus").className = `status-pill ${state.chain.valid ? "ok" : "bad"}`;
}

function renderValidators() {
  const list = $("validatorsList");
  const eligible = state.validators.filter((validator) => !validator.is_banned && validator.stake_locked > 0);
  text("eligibleCount", `${eligible.length} elegibles`);
  if (!state.validators.length) {
    list.innerHTML = `<div class="empty">Sin validadores registrados</div>`;
    return;
  }
  list.innerHTML = state.validators
    .slice(0, 12)
    .map(
      (validator) => `
        <article class="validator-row">
          <header>
            <strong class="mono">${escapeHtml(validator.validator_id)}</strong>
            <span class="status-pill ${validator.is_banned ? "bad" : "ok"}">${validator.is_banned ? "ban" : "activo"}</span>
          </header>
          <div class="validator-stats">
            <div><span>Score</span><strong>${fmt(validator.selection_score, 4)}</strong></div>
            <div><span>Trust</span><strong>${fmt(validator.trust_score, 4)}</strong></div>
            <div><span>Stake</span><strong>${fmt(validator.stake_locked, 5)}</strong></div>
            <div><span>Rewards</span><strong>${fmt(validator.total_rewards, 5)}</strong></div>
            <div><span>Votos recientes</span><strong>${fmt(validator.recent_validation_votes, 0)}</strong></div>
            <div><span>Avg ms</span><strong>${fmt(validator.avg_validation_ms, 0)}</strong></div>
          </div>
        </article>
      `
    )
    .join("");
}

function renderDifficulty() {
  text("difficultyAction", state.preview.action);
  $("difficultyAction").className = `status-pill ${state.preview.action === "wait" ? "warn" : "ok"}`;
  text("difficultyHeight", fmt(state.difficulty.current_height, 0));
  text("difficultyRemaining", fmt(state.difficulty.blocks_until_next_epoch, 0));
  text("difficultyAverage", state.difficulty.current_epoch_average_ms ? `${fmt(state.difficulty.current_epoch_average_ms, 0)} ms` : "-");
  text("difficultyTarget", `${fmt(state.difficulty.target_block_ms, 0)} ms`);
  const total = state.difficulty.epoch_blocks || 1;
  const current = state.difficulty.current_epoch_block_count || 0;
  $("epochProgress").style.width = `${Math.min(100, (current / total) * 100)}%`;
}

function renderPerformance() {
  const metrics = [
    ["Asignacion", state.performance.avg_assignment_ms],
    ["Compute", state.performance.avg_compute_ms],
    ["Commit", state.performance.avg_commit_ms],
    ["Validacion", state.performance.avg_validation_ms],
    ["Total", state.performance.avg_total_task_ms],
  ];
  const max = Math.max(1, ...metrics.map(([, value]) => Number(value || 0)));
  $("performanceBars").innerHTML = metrics
    .map(([label, value]) => {
      const width = Math.min(100, (Number(value || 0) / max) * 100);
      return `
        <div>
          <div class="bar-label"><span>${label}</span><strong>${fmt(value, 0)} ms</strong></div>
          <div class="bar-track"><span class="bar-fill" style="width: ${width}%"></span></div>
        </div>
      `;
    })
    .join("");
  text("cacheStats", `${state.performance.bbp_digit_cache_hits}/${state.performance.bbp_digit_cache_misses}`);
}

function renderAudit() {
  text("auditStatus", state.audit.valid ? "valida" : "issues");
  $("auditStatus").className = `status-pill ${state.audit.valid ? "ok" : "bad"}`;
  const issueSummary = state.audit.issues.length
    ? `${state.audit.issues.length} issues`
    : "sin issues";
  $("auditSummary").innerHTML = `
    <div class="audit-box"><span>Balances esperados</span><strong>${fmt(state.audit.supply.expected_total_balances, 5)}</strong></div>
    <div class="audit-box"><span>Balances actuales</span><strong>${fmt(state.audit.supply.actual_total_balances, 5)}</strong></div>
    <div class="audit-box"><span>Minted rewards</span><strong>${fmt(state.audit.rewards.total_minted_rewards, 5)}</strong></div>
    <div class="audit-box"><span>Estado</span><strong>${issueSummary}</strong></div>
  `;
}

async function submitFaucet(event) {
  event.preventDefault();
  const payload = {
    account_id: $("faucetAccount").value.trim(),
    account_type: $("faucetType").value,
    amount: Number($("faucetAmount").value),
  };
  $("faucetResult").textContent = "Enviando...";
  try {
    const result = await fetchJson("/faucet", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("faucetResult").textContent = JSON.stringify(result, null, 2);
    await loadData();
  } catch (error) {
    $("faucetResult").textContent = `Error: ${error.message}`;
  }
}

async function boot() {
  $("refreshButton").addEventListener("click", loadData);
  $("faucetForm").addEventListener("submit", submitFaucet);
  try {
    await loadData();
  } catch (error) {
    $("chainStatus").textContent = "sin conexion";
    $("chainStatus").className = "status-pill bad";
    $("nodeStatusBadge").textContent = "offline";
    $("nodeStatusBadge").className = "status-pill bad";
    $("faucetResult").textContent = `Error: ${error.message}`;
  }
}

boot();
