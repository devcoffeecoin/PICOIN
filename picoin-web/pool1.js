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
    creditedWorkers: document.getElementById("poolCreditedWorkers"),
    activeTasks: document.getElementById("poolActiveTasks"),
    completedTasks: document.getElementById("poolCompletedTasks"),
    pendingPayouts: document.getElementById("poolPendingPayouts"),
    fee: document.getElementById("poolFee"),
    minPayout: document.getElementById("poolMinPayout"),
    autoPayouts: document.getElementById("poolAutoPayouts"),
    error: document.getElementById("poolError"),
    sharesTable: document.getElementById("poolSharesTable"),
    payoutsTable: document.getElementById("poolPayoutsTable"),
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

  function pi(value) {
    const numeric = Number(value || 0);
    return `${numeric.toFixed(6)} PI`;
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

  function renderStatusRows(target, rows) {
    if (!rows.length) {
      target.innerHTML = `<tr><td colspan="2" class="empty">No records yet</td></tr>`;
      return;
    }
    target.innerHTML = rows
      .map(
        (row) => `
          <tr>
            <td><span class="status-pill">${escapeHtml(row.status)}</span></td>
            <td>${fmt(Number(row.count || 0), 0)}</td>
          </tr>
        `,
      )
      .join("");
  }

  function renderShares(shares) {
    const rows = Object.entries(shares || {}).sort((a, b) => Number(b[1].units || 0) - Number(a[1].units || 0));
    if (!rows.length) {
      els.sharesTable.innerHTML = `<tr><td colspan="3" class="empty">No credited shares yet</td></tr>`;
      return;
    }
    els.sharesTable.innerHTML = rows
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
    const activeTasks = statusCount(tasks, ["active", "gathering", "submitting", "validation_pending"]);
    const completedTasks = statusCount(tasks, ["accepted", "submitted", "validation_pending"]);
    const shares = stats.credited_shares || {};
    const payouts = stats.payouts || {};
    const autoPayouts = stats.auto_payouts || {};

    els.minerId.textContent = fmt(stats.miner_id);
    els.mainnet.textContent = fmt(stats.mainnet_server);
    els.workers.textContent = fmt(Number(stats.workers || 0), 0);
    els.creditedWorkers.textContent = fmt(Object.keys(shares).length, 0);
    els.activeTasks.textContent = fmt(activeTasks, 0);
    els.completedTasks.textContent = fmt(completedTasks, 0);
    els.pendingPayouts.textContent = pi(payouts.pending_total);
    els.fee.textContent = `${Number(payouts.pool_fee_percent || 0).toFixed(2)}%`;
    els.minPayout.textContent = pi(payouts.min_payout_amount);
    els.autoPayouts.textContent = autoPayouts.enabled
      ? `On / ${fmt(Number(autoPayouts.interval_seconds || 0), 0)}s`
      : "Off";

    renderShares(shares);
    renderPayouts(payouts);
    renderStatusRows(els.tasksTable, tasks);
    renderStatusRows(els.chunksTable, asArray(stats.chunks));
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
