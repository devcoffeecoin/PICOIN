const DEFAULT_TIMEOUT_MS = 8500;
const STALE_MS = 10 * 60 * 1000;
const cache = new Map();

function cleanTarget(value) {
  return String(value || "").replace(/\/$/, "");
}

function pathFromRequest(req) {
  const rawPath = req.query.path !== undefined ? req.query.path : req.query["...path"];
  const rawParts = Array.isArray(rawPath) ? rawPath : [rawPath].filter(Boolean);
  const parts = rawParts.flatMap((part) => String(part || "").split("/").filter(Boolean));
  return `/${parts.map(encodeURIComponent).join("/")}`;
}

function buildTargetUrl(req, target) {
  const url = new URL(pathFromRequest(req), `${cleanTarget(target)}/`);
  for (const [key, value] of Object.entries(req.query)) {
    if (key === "path" || key === "...path") continue;
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (item !== undefined) url.searchParams.append(key, item);
    }
  }
  return url;
}

function cachePolicy(pathname) {
  const path = pathname.toLowerCase();
  if (path === "/audit/full") return { ttlMs: 120000, browserMaxAge: 30 };
  if (path === "/treasury/status" || path === "/reserve/status") return { ttlMs: 60000, browserMaxAge: 20 };
  if (path === "/validators/status" || path === "/miners/status" || path === "/stats") {
    return { ttlMs: 20000, browserMaxAge: 8 };
  }
  if (path === "/blocks" || path.startsWith("/blocks?") || path === "/mining/metrics") {
    return { ttlMs: 15000, browserMaxAge: 5 };
  }
  if (path.startsWith("/validators") || path.startsWith("/miners")) return { ttlMs: 45000, browserMaxAge: 15 };
  if (path.startsWith("/transactions/recent") || path.startsWith("/mempool")) {
    return { ttlMs: 8000, browserMaxAge: 3 };
  }
  if (path === "/health" || path === "/node/sync-status" || path === "/protocol" || path === "/difficulty") {
    return { ttlMs: 5000, browserMaxAge: 2 };
  }
  return { ttlMs: 10000, browserMaxAge: 3 };
}

function setCommonHeaders(res, policy, source) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Accept");
  res.setHeader(
    "Cache-Control",
    `public, max-age=${policy.browserMaxAge}, s-maxage=${Math.max(policy.browserMaxAge, 10)}, stale-while-revalidate=60`
  );
  res.setHeader("X-Picoin-Cache", source);
}

function sendCached(res, record, policy, source) {
  setCommonHeaders(res, policy, source);
  res.status(record.status);
  res.setHeader("Content-Type", record.contentType || "application/json");
  res.send(Buffer.from(record.body));
}

function getFreshRecord(key, policy) {
  const record = cache.get(key);
  if (!record) return null;
  return Date.now() - record.storedAt <= policy.ttlMs ? record : null;
}

function getStaleRecord(key) {
  const record = cache.get(key);
  if (!record) return null;
  return Date.now() - record.storedAt <= STALE_MS ? record : null;
}

async function proxyHandler(req, res, options) {
  if (req.method === "OPTIONS") {
    setCommonHeaders(res, { browserMaxAge: 30 }, "options");
    return res.status(204).end();
  }

  if (req.method !== "GET" && req.method !== "HEAD") {
    return res.status(405).json({ detail: "Method not allowed" });
  }

  const targetUrl = buildTargetUrl(req, options.target);
  const policy = cachePolicy(targetUrl.pathname);
  const cacheKey = `${req.method}:${targetUrl.toString()}`;
  const fresh = req.method === "GET" ? getFreshRecord(cacheKey, policy) : null;
  if (fresh) return sendCached(res, fresh, policy, "hit");

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs || DEFAULT_TIMEOUT_MS);

  try {
    const upstream = await fetch(targetUrl, {
      method: req.method,
      headers: { Accept: req.headers.accept || "application/json" },
      signal: controller.signal,
    });
    const body = req.method === "HEAD" ? null : Buffer.from(await upstream.arrayBuffer());
    const contentType = upstream.headers.get("content-type") || "application/json";

    if (req.method === "GET" && upstream.ok && body) {
      cache.set(cacheKey, {
        status: upstream.status,
        contentType,
        body,
        storedAt: Date.now(),
      });
    }

    if (!upstream.ok && req.method === "GET" && (upstream.status >= 500 || upstream.status === 429)) {
      const stale = getStaleRecord(cacheKey);
      if (stale) return sendCached(res, stale, policy, "stale");
    }

    setCommonHeaders(res, policy, upstream.ok ? "miss" : "bypass");
    res.status(upstream.status);
    res.setHeader("Content-Type", contentType);
    if (body) res.send(body);
    else res.end();
  } catch (error) {
    const stale = req.method === "GET" ? getStaleRecord(cacheKey) : null;
    if (stale) return sendCached(res, stale, policy, "stale");

    setCommonHeaders(res, policy, "error");
    const status = error && error.name === "AbortError" ? 504 : 502;
    res.status(status).json({
      detail: `${options.name} unavailable`,
      error: error && error.message ? error.message : String(error),
      upstream: targetUrl.toString(),
    });
  } finally {
    clearTimeout(timeout);
  }
}

module.exports = {
  proxyHandler,
};
