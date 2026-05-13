function defaultState() {
  return {
    protocol: {
      network_id: "-",
      protocol_version: "-",
      difficulty: 0,
      faucet_enabled: false,
      retroactive_audit_interval_blocks: 0,
      retroactive_audit_reward_per_audit: 0,
    },
    stats: {
      accepted_blocks: 0,
      total_minted_rewards: 0,
    },
    performance: {
      avg_assignment_ms: 0,
      avg_compute_ms: 0,
      avg_commit_ms: 0,
      avg_validation_ms: 0,
      avg_total_task_ms: 0,
      bbp_digit_cache_hits: 0,
      bbp_digit_cache_misses: 0,
    },
    difficulty: {
      current_height: 0,
      blocks_until_next_epoch: 0,
      current_epoch_average_ms: 0,
      target_block_ms: 0,
      epoch_blocks: 1,
      current_epoch_block_count: 0,
    },
    preview: { action: "wait" },
    blocks: [],
    validators: [],
    audit: {
      valid: false,
      issues: [],
      supply: {
        expected_total_balances: 0,
        actual_total_balances: 0,
      },
      rewards: {
        total_minted_rewards: 0,
        science_reserve_total: 0,
        audit_reward_total: 0,
      },
    },
    retroAudits: [],
    chain: { valid: false, issues: [] },
    health: {
      status: "loading",
      mining_ready: false,
      can_assign_tasks: false,
    },
    node: {
      uptime_seconds: 0,
      latest_block_height: 0,
      chain_valid: false,
      counts: {
        eligible_validators: 0,
        required_validator_approvals: 0,
        validation_jobs: { pending: 0 },
      },
    },
    events: [],
    treasury: {
      locked_balance: 0,
      unlocked_balance: 0,
      next_unlock_at: null,
    },
    reserve: {
      available: 0,
      total_paid: 0,
    },
    sync: {
      chain_id: "-",
      node_id: "-",
      pending_replay_blocks: 0,
      peer_counts: { connected: 0, total: 0 },
      mempool: { pending: 0 },
      consensus: { pending: 0, finalized: 0, imported: 0 },
    },
    peers: [],
    mempool: [],
    apiErrors: [],
  };
}

const state = defaultState();

const DATA_ENDPOINTS = {
  protocol: "/protocol",
  stats: "/stats",
  performance: "/stats/performance",
  difficulty: "/difficulty",
  preview: "/difficulty/preview",
  blocks: "/blocks",
  validators: "/validators?limit=100",
  audit: "/audit/full",
  retroAudits: "/audit/retroactive?limit=8",
  chain: "/blocks/verify",
  health: "/health",
  node: "/node/status",
  events: "/events?limit=14",
  treasury: "/treasury/status",
  reserve: "/reserve/status",
  sync: "/node/sync-status",
  peers: "/node/peers",
  mempool: "/mempool?limit=12",
};

const $ = (id) => document.getElementById(id);

function text(id, value) {
  const element = $(id);
  if (element) element.textContent = value;
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

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString();
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
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail || response.statusText || `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload;
}

async function safeFetchJson(key, path) {
  try {
    return { key, value: await fetchJson(path) };
  } catch (error) {
    return {
      key,
      value: defaultState()[key],
      error: { path, message: error.message },
    };
  }
}

async function loadData() {
  text("connectionBadge", "conectando");
  $("connectionBadge").className = "status-pill warn";

  const results = await Promise.all(
    Object.entries(DATA_ENDPOINTS).map(([key, path]) => safeFetchJson(key, path))
  );
  const apiErrors = [];

  results.forEach((result) => {
    state[result.key] = result.value;
    if (result.error) apiErrors.push(result.error);
  });
  state.apiErrors = apiErrors;
  render();
}

function render() {
  renderConnection();
  renderMetrics();
  renderBlocks();
  renderNode();
  renderNetwork();
  renderEvents();
  renderValidators();
  renderDifficulty();
  renderPerformance();
  renderAudit();
}

function renderConnection() {
  const badge = $("connectionBadge");
  if (!state.apiErrors.length) {
    badge.textContent = "conectado";
    badge.className = "status-pill ok";
    $("dashboardErrors").innerHTML = "";
    return;
  }

  badge.textContent = `${state.apiErrors.length} endpoint${state.apiErrors.length === 1 ? "" : "s"} con error`;
  badge.className = "status-pill warn";
  $("dashboardErrors").innerHTML = state.apiErrors
    .slice(0, 5)
    .map(
      (error) => `
        <div class="error-row">
          <strong>${escapeHtml(error.path)}</strong>: ${escapeHtml(error.message)}
        </div>
      `
    )
    .join("");
}

function renderMetrics() {
  const status = state.health.status || "offline";
  text("networkBadge", `${state.protocol.network_id} - v${state.protocol.protocol_version}`);
  text("nodeStatusBadge", status);
  $("nodeStatusBadge").className = `status-pill ${status === "ok" ? "ok" : "warn"}`;
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

function renderBlocks() {
  const tbody = $("blocksTable");
  const recent = [...state.blocks]
    .sort((a, b) => Number(b.height || 0) - Number(a.height || 0))
    .slice(0, 50);
  if (!recent.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">Sin bloques aceptados</td></tr>`;
  } else {
    tbody.innerHTML = recent
      .map(
        (block) => `
          <tr>
            <td>${fmt(block.height, 0)}</td>
            <td class="hash" title="${escapeHtml(block.block_hash)}">${escapeHtml(shortHash(block.block_hash))}</td>
            <td class="hash" title="${escapeHtml(block.previous_hash)}">${escapeHtml(shortHash(block.previous_hash))}</td>
            <td class="mono">${escapeHtml(block.miner_id)}</td>
            <td>${fmt(block.range_start, 0)}..${fmt(block.range_end, 0)}</td>
            <td>${fmt(block.reward, 5)}</td>
            <td>${fmt(block.tx_count || 0, 0)}</td>
            <td class="hash" title="${escapeHtml(block.state_root)}">${escapeHtml(shortHash(block.state_root))}</td>
            <td>${escapeHtml(formatDate(block.timestamp))}</td>
          </tr>
        `
      )
      .join("");
  }
  text("chainStatus", state.chain.valid ? "cadena valida" : "revisar cadena");
  $("chainStatus").className = `status-pill ${state.chain.valid ? "ok" : "bad"}`;
}

function renderNode() {
  text("nodeHealth", state.health.mining_ready ? "mining ready" : state.health.status);
  $("nodeHealth").className = `status-pill ${state.health.mining_ready ? "ok" : "warn"}`;
  const counts = state.node.counts || {};
  const validationJobs = counts.validation_jobs || {};
  $("nodeSummary").innerHTML = `
    <div class="summary-box"><span>Uptime</span><strong>${fmt(state.node.uptime_seconds, 0)} s</strong></div>
    <div class="summary-box"><span>Altura</span><strong>${fmt(state.node.latest_block_height, 0)}</strong></div>
    <div class="summary-box"><span>Validadores elegibles</span><strong>${fmt(counts.eligible_validators, 0)} / ${fmt(counts.required_validator_approvals, 0)}</strong></div>
    <div class="summary-box"><span>Jobs pendientes</span><strong>${fmt(validationJobs.pending, 0)}</strong></div>
    <div class="summary-box"><span>Asignacion</span><strong>${state.health.can_assign_tasks ? "activa" : "bloqueada"}</strong></div>
    <div class="summary-box"><span>Cadena</span><strong>${state.node.chain_valid ? "valida" : "revisar"}</strong></div>
  `;
}

function renderNetwork() {
  const peerCounts = state.sync.peer_counts || {};
  const connected = peerCounts.connected || 0;
  const total = peerCounts.total || 0;
  text("peerStatus", `${connected}/${total} peers`);
  $("peerStatus").className = `status-pill ${connected > 0 ? "ok" : "warn"}`;
  $("networkSummary").innerHTML = `
    <div class="summary-box"><span>Chain ID</span><strong>${escapeHtml(state.sync.chain_id)}</strong></div>
    <div class="summary-box"><span>Node ID</span><strong>${escapeHtml(state.sync.node_id)}</strong></div>
    <div class="summary-box"><span>Mempool pending</span><strong>${fmt((state.sync.mempool || {}).pending || 0, 0)}</strong></div>
    <div class="summary-box"><span>Replay queue</span><strong>${fmt(state.sync.pending_replay_blocks, 0)}</strong></div>
    <div class="summary-box"><span>Propuestas</span><strong>${fmt((state.sync.consensus || {}).pending || 0, 0)}</strong></div>
    <div class="summary-box"><span>Finalizados</span><strong>${fmt((state.sync.consensus || {}).finalized || (state.sync.consensus || {}).imported || 0, 0)}</strong></div>
  `;
  if (!state.peers.length) {
    $("peersList").innerHTML = `<div class="empty">Sin peers registrados</div>`;
    return;
  }
  $("peersList").innerHTML = state.peers
    .slice(0, 6)
    .map(
      (peer) => `
        <article class="event-row">
          <header>
            <strong>${escapeHtml(peer.peer_type)}:${escapeHtml(peer.node_id)}</strong>
            <span class="status-pill ${peer.status === "connected" ? "ok" : "warn"}">${escapeHtml(peer.status)}</span>
          </header>
          <p class="mono">${escapeHtml(peer.peer_address)}</p>
        </article>
      `
    )
    .join("");
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
        <article class="event-row ${escapeHtml(event.severity)}">
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

function renderValidators() {
  const list = $("validatorsList");
  const eligible = state.validators.filter((validator) => !validator.is_banned && validator.stake_locked > 0);
  text("eligibleCount", `${eligible.length} elegibles`);
  if (!state.validators.length) {
    list.innerHTML = `<div class="empty">Sin validadores registrados</div>`;
    return;
  }
  list.innerHTML = state.validators
    .slice(0, 6)
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
  const issues = state.audit.issues || [];
  const supply = state.audit.supply || {};
  const rewards = state.audit.rewards || {};
  text("auditStatus", state.audit.valid ? "valida" : "issues");
  $("auditStatus").className = `status-pill ${state.audit.valid ? "ok" : "bad"}`;
  const issueSummary = issues.length ? `${issues.length} issues` : "sin issues";
  const latestRetroAudit = state.retroAudits[0];
  $("auditSummary").innerHTML = `
    <div class="audit-box"><span>Balances esperados</span><strong>${fmt(supply.expected_total_balances, 5)}</strong></div>
    <div class="audit-box"><span>Balances actuales</span><strong>${fmt(supply.actual_total_balances, 5)}</strong></div>
    <div class="audit-box"><span>Minted rewards</span><strong>${fmt(rewards.total_minted_rewards, 5)}</strong></div>
    <div class="audit-box"><span>Science reserve</span><strong>${fmt(rewards.science_reserve_total, 5)}</strong></div>
    <div class="audit-box"><span>Reserve disponible</span><strong>${fmt(state.reserve.available, 5)}</strong></div>
    <div class="audit-box"><span>Compute pagado</span><strong>${fmt(state.reserve.total_paid, 5)}</strong></div>
    <div class="audit-box"><span>Treasury locked</span><strong>${fmt(state.treasury.locked_balance, 5)}</strong></div>
    <div class="audit-box"><span>Treasury unlocked</span><strong>${fmt(state.treasury.unlocked_balance, 5)}</strong></div>
    <div class="audit-box"><span>Next unlock</span><strong>${escapeHtml(formatDate(state.treasury.next_unlock_at))}</strong></div>
    <div class="audit-box"><span>Audit rewards</span><strong>${fmt(rewards.audit_reward_total, 5)}</strong></div>
    <div class="audit-box"><span>Estado</span><strong>${issueSummary}</strong></div>
    <div class="audit-box"><span>Retro audits</span><strong>${fmt(state.retroAudits.length, 0)}</strong></div>
    <div class="audit-box"><span>Ultima retro</span><strong>${latestRetroAudit ? `#${latestRetroAudit.block_height} ${latestRetroAudit.passed ? "OK" : "Fraud"}` : "-"}</strong></div>
    <div class="audit-box"><span>Cadencia</span><strong>${fmt(state.protocol.retroactive_audit_interval_blocks, 0)} bloques</strong></div>
    <div class="audit-box"><span>Reward auditoria</span><strong>${fmt(state.protocol.retroactive_audit_reward_per_audit, 5)}</strong></div>
  `;
}

async function runRetroAudit() {
  $("retroAuditButton").disabled = true;
  try {
    await fetchJson("/audit/retroactive/run?sample_multiplier=2", { method: "POST" });
    await loadData();
  } catch (error) {
    $("faucetResult").textContent = `Retro audit error: ${error.message}`;
  } finally {
    $("retroAuditButton").disabled = false;
  }
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
  $("retroAuditButton").addEventListener("click", runRetroAudit);
  $("faucetForm").addEventListener("submit", submitFaucet);
  await loadData();
}

boot();
