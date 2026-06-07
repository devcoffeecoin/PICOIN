(function () {
  const params = new URLSearchParams(window.location.search);
  const apiBaseUrl = cleanUrl(params.get("api") || "/api/pool1");
  const refreshMs = 10000;

  const els = {
    status: document.getElementById("poolStatus"),
    endpoint: document.getElementById("poolEndpoint"),
    minerId: document.getElementById("poolMinerId"),
    mainnet: document.getElementById("poolMainnet"),
    workers: document.getElementById("poolWorkers"),
    chunking: document.getElementById("poolChunking"),
    hashrate: document.getElementById("poolHashrate"),
    creditedWorkers: document.getElementById("poolCreditedWorkers"),
    activeTasks: document.getElementById("poolActiveTasks"),
    completedTasks: document.getElementById("poolCompletedTasks"),
    blocksWon: document.getElementById("poolBlocksWon"),
    winRate: document.getElementById("poolWinRate"),
    pendingValidation: document.getElementById("poolPendingValidation"),
    pendingPayouts: document.getElementById("poolPendingPayouts"),
    fee: document.getElementById("poolFee"),
    minPayout: document.getElementById("poolMinPayout"),
    autoPayouts: document.getElementById("poolAutoPayouts"),
    accountingMode: document.getElementById("poolAccountingMode"),
    error: document.getElementById("poolError"),
    activeWorkersTable: document.getElementById("poolActiveWorkersTable"),
    currentWindow: document.getElementById("poolCurrentWindow"),
    currentSharesTable: document.getElementById("poolCurrentSharesTable"),
    payoutsTable: document.getElementById("poolPayoutsTable"),
    wonBlocksTable: document.getElementById("poolWonBlocksTable"),
    tasksTable: document.getElementById("poolTasksTable"),
    chunksTable: document.getElementById("poolChunksTable"),
    eventsTable: document.getElementById("poolEventsTable"),
    updated: document.getElementById("poolUpdated"),
  };

  els.endpoint.textContent = apiBaseUrl;

  function cleanUrl(value) {
    return String(value || "").replace(/\/+$/, "");
  }

  function fmt(value, fallback = "-") {
    if (value === null || value === undefined || value === "") return fallback;
    if (typeof value === "number") return new Intl.NumberFormat("en-US").format(value);
    return String(value);
  }

  function pct(value) {
    const numeric = Number(value || 0);
    return `${(numeric * 100).toFixed(2)}%`;
  }

  function feePct(value) {
    const numeric = Number(value || 0);
    if (!Number.isFinite(numeric)) return "-";
    return `${numeric.toLocaleString("en-US", { maximumFractionDigits: 6 })}%`;
  }

  function pi(value) {
    const numeric = Number(value || 0);
    return `${numeric.toFixed(6)} PI`;
  }

  function rate(value) {
    const numeric = Number(value || 0);
    if (!Number.isFinite(numeric) || numeric <= 0) return "0 H/s";
    if (numeric >= 1000000) return `${fmt(numeric / 1000000, 2)} MH/s`;
    if (numeric >= 1000) return `${fmt(numeric / 1000, 2)} kH/s`;
    return `${fmt(numeric, 2)} H/s`;
  }

  function shortHash(value, size = 10) {
    const text = String(value || "");
    if (text.length <= size * 2 + 3) return text || "-";
    return `${text.slice(0, size)}...${text.slice(-size)}`;
  }

  function timeAgo(value) {
    const date = new Date(value || 0);
    if (!Number.isFinite(date.getTime())) return "-";
    const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return `${hours}h ago`;
    return date.toLocaleString();
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function updateBadge(kind, text) {
    els.status.className = `status-pill ${kind}`;
    els.status.textContent = text;
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function statusCount(items, names) {
    const wanted = new Set(names);
    return asArray(items).reduce((total, item) => {
      return total + (wanted.has(String(item.status || "")) ? Number(item.count || 0) : 0);
    }, 0);
  }

  function totalStatusCount(items) {
    return asArray(items).reduce((total, item) => total + Number(item.count || 0), 0);
  }

  function completedTaskFallback(tasks) {
    const active = statusCount(tasks, ["active", "gathering", "submitting"]);
    const pendingValidation = statusCount(tasks, ["validation_pending"]);
    return Math.max(0, totalStatusCount(tasks) - active - pendingValidation);
  }

  async function fetchJson(path) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(`${apiBaseUrl}${path}`, {
        headers: { Accept: "application/json" },
        mode: "cors",
        signal: controller.signal,
      });
      const text = await response.text();
      const data = text ? JSON.parse(text) : {};
      if (!response.ok) {
        throw new Error(data.detail || response.statusText || `HTTP ${response.status}`);
      }
      return data;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  const taskMeanings = {
    accepted: "Reveal accepted; reward counts only when a final block exists",
    archived: "Closed pool round kept off the active window",
    error: "Submission failed before reaching mainnet",
    expired: "Mainnet expired the task before it became a block",
    gathering: "Workers are computing chunks for this task",
    lost: "Another miner won that competitive block",
    rejected: "Mainnet rejected the submission",
    stale: "Mainnet closed the competitive round before this task became a block",
    submitted: "Submitted and waiting for final state",
    submitting: "Pool is assembling and submitting the answer",
    unsettled: "Reveal without a final block recorded by the pool",
    validation_pending: "External validators are checking the reveal",
  };

  const chunkMeanings = {
    archived: "Closed worker chunk kept off the active window",
    assigned: "A worker is computing this chunk",
    completed: "Returned by worker and accepted by pool",
    pending: "Available for active workers",
  };

  function renderStatusRows(target, rows, meanings) {
    if (!rows.length) {
      target.innerHTML = `<tr><td colspan="3" class="empty">No records yet</td></tr>`;
      return;
    }
    target.innerHTML = rows
      .map(
        (row) => `
          <tr>
            <td><span class="status-pill">${escapeHtml(row.status)}</span></td>
            <td>${fmt(Number(row.count || 0), 0)}</td>
            <td>${escapeHtml(meanings[row.status] || "-")}</td>
          </tr>
        `,
      )
      .join("");
  }

  function renderActiveWorkers(workers) {
    const rows = asArray(workers);
    if (!rows.length) {
      els.activeWorkersTable.innerHTML = `<tr><td colspan="4" class="empty">No active workers in the last 5 minutes</td></tr>`;
      return;
    }
    els.activeWorkersTable.innerHTML = rows
      .map(
        (worker) => `
          <tr>
            <td class="mono">${escapeHtml(worker.worker_id)}</td>
            <td>${escapeHtml(worker.name || "-")}</td>
            <td class="mono" title="${escapeHtml(worker.payout_address || "-")}">${escapeHtml(shortHash(worker.payout_address, 8))}</td>
            <td>${escapeHtml(timeAgo(worker.last_seen_at))}</td>
          </tr>
        `,
      )
      .join("");
  }

  function renderShares(target, shares) {
    const rows = Object.entries(shares || {}).sort((a, b) => Number(b[1].units || 0) - Number(a[1].units || 0));
    if (!rows.length) {
      target.innerHTML = `<tr><td colspan="3" class="empty">No shares in the current reward window</td></tr>`;
      return;
    }
    target.innerHTML = rows
      .map(
        ([workerId, share]) => `
          <tr>
            <td class="mono">${escapeHtml(workerId)}</td>
            <td>${fmt(Number(share.units || 0), 0)}</td>
            <td>${pct(share.fraction)}</td>
          </tr>
        `,
      )
      .join("");
  }

  function formatWindowStart(value) {
    if (!value) return "Open from pool start until the first Pool 1 block";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "Open reward window";
    return `Open since latest Pool 1 block: ${date.toLocaleString()}`;
  }

  function renderPayouts(payouts) {
    const rows = asArray(payouts && payouts.workers);
    if (!rows.length) {
      els.payoutsTable.innerHTML = `<tr><td colspan="4" class="empty">No pending payouts yet</td></tr>`;
      return;
    }
    els.payoutsTable.innerHTML = rows
      .map(
        (worker) => `
          <tr>
            <td class="mono">${escapeHtml(worker.worker_id)}</td>
            <td class="mono">${escapeHtml(worker.payout_address || "-")}</td>
            <td>${fmt(Number(worker.units || 0), 0)}</td>
            <td>${pi(worker.pending_amount)}</td>
          </tr>
        `,
      )
      .join("");
  }

  function renderWonBlocks(blocks) {
    const rows = asArray(blocks);
    if (!rows.length) {
      els.wonBlocksTable.innerHTML = `<tr><td colspan="5" class="empty">No won blocks with final rewards yet</td></tr>`;
      return;
    }
    els.wonBlocksTable.innerHTML = rows
      .map(
        (block) => `
          <tr>
            <td>${fmt(block.height)}</td>
            <td>${pi(block.reward)}</td>
            <td class="mono" title="${escapeHtml(block.block_hash || "-")}">${escapeHtml(shortHash(block.block_hash, 8))}</td>
            <td class="mono">${escapeHtml(block.mainnet_task_id || block.pool_task_id || "-")}</td>
            <td>${escapeHtml(timeAgo(block.completed_at))}</td>
          </tr>
        `,
      )
      .join("");
  }

  function renderEvents(events) {
    const rows = asArray(events);
    if (!rows.length) {
      els.eventsTable.innerHTML = `<tr><td colspan="3" class="empty">No pool events yet</td></tr>`;
      return;
    }
    els.eventsTable.innerHTML = rows
      .map(
        (event) => `
          <tr>
            <td>${escapeHtml(event.created_at || "-")}</td>
            <td><span class="status-pill">${escapeHtml(event.level || "-")}</span></td>
            <td>${escapeHtml(event.message || "-")}</td>
          </tr>
        `,
      )
      .join("");
  }

  function render(stats) {
    const tasks = asArray(stats.tasks);
    const currentShares = stats.current_round_shares || stats.credited_shares || {};
    const payouts = stats.payouts || {};
    const autoPayouts = stats.auto_payouts || {};
    const performance = stats.performance || {};
    const chunking = stats.chunking || {};
    const hashrate = stats.hashrate || {};
    const windows = stats.share_windows || {};

    els.minerId.textContent = fmt(stats.miner_id);
    els.mainnet.textContent = fmt(stats.mainnet_server);
    els.workers.textContent = `${fmt(Number(performance.active_workers || stats.active_workers || 0), 0)} / ${fmt(Number(stats.workers || 0), 0)}`;
    els.chunking.textContent = chunking.mode === "fixed"
      ? `Fixed / ${fmt(chunking.fixed_chunk_size)}`
      : "Auto";
    els.hashrate.textContent = rate(performance.pool_hashrate_hps || hashrate.pool_hashrate_hps);
    els.creditedWorkers.textContent = fmt(Object.keys(currentShares).length, 0);
    els.activeTasks.textContent = fmt(Number(performance.active_tasks ?? statusCount(tasks, ["active", "gathering", "submitting"])), 0);
    els.completedTasks.textContent = fmt(Number(performance.completed_tasks ?? completedTaskFallback(tasks)), 0);
    els.blocksWon.textContent = fmt(Number(performance.blocks_won || 0), 0);
    els.winRate.textContent = `${Number(performance.win_rate_percent || 0).toFixed(2)}%`;
    els.pendingValidation.textContent = fmt(Number(performance.validation_pending_tasks || 0), 0);
    els.pendingPayouts.textContent = pi(payouts.pending_total);
    els.fee.textContent = feePct(payouts.pool_fee_percent);
    els.minPayout.textContent = pi(payouts.min_payout_amount);
    els.autoPayouts.textContent = autoPayouts.enabled
      ? `On / ${fmt(Number(autoPayouts.interval_seconds || 0), 0)}s`
      : "Off";
    els.accountingMode.textContent = fmt(payouts.accounting_mode || "current window");
    els.currentWindow.textContent = formatWindowStart(windows.current_start_at);

    renderActiveWorkers(stats.active_worker_details);
    renderShares(els.currentSharesTable, currentShares);
    renderPayouts(payouts);
    renderWonBlocks(stats.won_blocks);
    renderStatusRows(els.tasksTable, tasks, taskMeanings);
    renderStatusRows(els.chunksTable, asArray(stats.chunks), chunkMeanings);
    renderEvents(stats.events);
    els.updated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  }

  async function refresh() {
    try {
      const health = await fetchJson("/health");
      const stats = await fetchJson("/stats");
      render(stats);
      els.error.textContent = "";
      updateBadge(health.status === "ok" && stats.status === "ok" ? "ok" : "warn", stats.status || health.status || "Unknown");
    } catch (error) {
      updateBadge("bad", "Offline");
      els.error.innerHTML = `<strong>Pool API error</strong>: ${escapeHtml(error.message || String(error))}`;
    }
  }

  refresh();
  window.setInterval(refresh, refreshMs);
})();
