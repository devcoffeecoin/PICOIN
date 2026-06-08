import assert from "node:assert/strict";

const DEFAULT_BASE_URL = "https://picoin.science";
const DEFAULT_ROUTES = [
  "/api/bootstrap",
];
const READ_PATHS = [
  "health",
  "protocol",
  "node/sync-status",
  "blocks?limit=5",
  "validators/status",
  "miners/status",
  "stats",
];

function parseArgs(argv) {
  const args = {
    baseUrl: DEFAULT_BASE_URL,
    routes: DEFAULT_ROUTES,
    required: DEFAULT_ROUTES.length,
    allowedLag: 0,
    timeoutMs: 20000,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--base-url") args.baseUrl = argv[++i];
    else if (arg === "--required") args.required = Number(argv[++i]);
    else if (arg === "--allowed-lag") args.allowedLag = Number(argv[++i]);
    else if (arg === "--timeout-ms") args.timeoutMs = Number(argv[++i]);
    else if (arg === "--routes") args.routes = argv[++i].split(",").map((value) => value.trim()).filter(Boolean);
    else if (!arg.startsWith("--")) args.baseUrl = arg;
  }

  return args;
}

function cleanUrl(value) {
  return String(value || "").replace(/\/+$/, "");
}

function routeUrl(baseUrl, route, path) {
  return `${cleanUrl(baseUrl)}${route}/${path}`;
}

async function fetchJson(url, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    const text = await response.text();
    let payload = {};
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (_error) {
        payload = { detail: text };
      }
    }
    if (!response.ok) {
      throw new Error(payload.detail || response.statusText || `HTTP ${response.status}`);
    }
    return payload;
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error(`timeout after ${timeoutMs}ms`);
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function effectiveHeight(sync) {
  return Number(sync?.effective_latest_block_height ?? sync?.latest_block_height ?? 0);
}

function effectiveHash(sync) {
  return sync?.effective_latest_block_hash || sync?.latest_block_hash || "";
}

function healthAcceptable(health) {
  if (health?.status === "ok") return true;
  const issues = Array.isArray(health?.issues) ? health.issues : [];
  return health?.status === "degraded" && issues.every((issue) => /not enough eligible validators for quorum/i.test(String(issue)));
}

async function checkRoute(baseUrl, route, timeoutMs) {
  const timings = {};
  const responses = {};

  for (const path of READ_PATHS) {
    const started = Date.now();
    responses[path] = await fetchJson(routeUrl(baseUrl, route, path), timeoutMs);
    timings[path] = Number(((Date.now() - started) / 1000).toFixed(3));
  }

  const health = responses.health;
  const protocol = responses.protocol;
  const sync = responses["node/sync-status"];
  const blocks = responses["blocks?limit=5"];

  assert.ok(healthAcceptable(health), `${route} health must be ok or read-only degraded`);
  assert.equal(protocol.network_id, sync.network_id, `${route} protocol/sync network_id mismatch`);
  assert.equal(String(protocol.chain_id), String(sync.chain_id), `${route} protocol/sync chain_id mismatch`);
  assert.equal(protocol.protocol_version, sync.protocol_version, `${route} protocol/sync protocol_version mismatch`);
  assert.equal(sync.genesis_hash, "da286143167d14044c053fbb23fcf4673bb11bcd34fb1a11e5510ee8f8edb6e7", `${route} unexpected genesis`);
  assert.equal(sync.replay?.sync_status, "healthy", `${route} replay must be healthy`);
  assert.equal(Boolean(sync.replay?.divergence_detected), false, `${route} must not be divergent`);
  assert.ok(Array.isArray(blocks), `${route} blocks response must be an array`);

  return {
    route,
    endpoint: `${cleanUrl(baseUrl)}${route}`,
    height: effectiveHeight(sync),
    hash: effectiveHash(sync),
    network_id: sync.network_id,
    chain_id: sync.chain_id,
    genesis_hash: sync.genesis_hash,
    protocol_version: sync.protocol_version,
    timings,
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const routes = args.routes;
  assert.ok(routes.length >= args.required, `configured route count ${routes.length} is lower than required ${args.required}`);

  const results = [];
  const errors = [];

  for (const route of routes) {
    try {
      results.push(await checkRoute(args.baseUrl, route, args.timeoutMs));
    } catch (error) {
      errors.push({ route, error: error.message || String(error) });
    }
  }

  const maxHeight = Math.max(0, ...results.map((result) => result.height));
  const reference = results.find((result) => result.height === maxHeight) || results[0];
  const checks = [];

  checks.push({
    name: "required_web_routes",
    ok: results.length >= args.required,
    detail: `healthy=${results.length} required=${args.required}`,
  });

  for (const result of results) {
    checks.push({
      name: "route_lag_within_limit",
      route: result.route,
      ok: maxHeight - result.height <= args.allowedLag,
      detail: `height=${result.height} max=${maxHeight} lag=${maxHeight - result.height} allowed=${args.allowedLag}`,
    });
    checks.push({
      name: "route_identity_matches_reference",
      route: result.route,
      ok:
        result.network_id === reference.network_id &&
        String(result.chain_id) === String(reference.chain_id) &&
        result.genesis_hash === reference.genesis_hash &&
        result.protocol_version === reference.protocol_version,
      detail: `network=${result.network_id} chain=${result.chain_id} protocol=${result.protocol_version}`,
    });
    checks.push({
      name: "route_tip_hash_matches_reference",
      route: result.route,
      ok: result.height !== reference.height || result.hash === reference.hash,
      detail: `hash=${result.hash} reference=${reference.hash}`,
    });
  }

  const failures = [...errors.map((error) => ({ ...error, ok: false, name: "route_responds" })), ...checks.filter((check) => !check.ok)];
  const output = {
    base_url: cleanUrl(args.baseUrl),
    required: args.required,
    allowed_lag: args.allowedLag,
    status: failures.length ? "fail" : "ok",
    summary: {
      routes_checked: routes.length,
      healthy_routes: results.length,
      errors: failures.length,
    },
    routes: results,
    errors,
    checks,
  };

  console.log(JSON.stringify(output, null, 2));
  process.exitCode = failures.length ? 1 : 0;
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
