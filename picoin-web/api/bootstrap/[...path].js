const TARGET = process.env.PICOIN_BOOTSTRAP_API_URL || "http://198.211.100.89:8000";

function buildTargetUrl(req) {
  const parts = Array.isArray(req.query.path) ? req.query.path : [req.query.path].filter(Boolean);
  const url = new URL(parts.map(encodeURIComponent).join("/"), `${TARGET.replace(/\/$/, "")}/`);

  for (const [key, value] of Object.entries(req.query)) {
    if (key === "path") continue;
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (item !== undefined) url.searchParams.append(key, item);
    }
  }

  return url;
}

module.exports = async function handler(req, res) {
  if (req.method === "OPTIONS") {
    res.setHeader("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type, Accept");
    return res.status(204).end();
  }

  if (req.method !== "GET" && req.method !== "HEAD") {
    return res.status(405).json({ detail: "Method not allowed" });
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);

  try {
    const upstream = await fetch(buildTargetUrl(req), {
      method: req.method,
      headers: { Accept: req.headers.accept || "application/json" },
      signal: controller.signal,
    });
    const body = req.method === "HEAD" ? null : await upstream.arrayBuffer();

    res.status(upstream.status);
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Content-Type", upstream.headers.get("content-type") || "application/json");
    if (body) res.send(Buffer.from(body));
    else res.end();
  } catch (error) {
    res.status(502).json({ detail: "Picoin bootstrap API unavailable", error: error.message });
  } finally {
    clearTimeout(timeout);
  }
};
