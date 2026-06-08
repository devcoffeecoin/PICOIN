import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

function makeStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
  };
}

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status >= 200 && status < 300 ? "OK" : "Gateway Timeout",
    async text() {
      return JSON.stringify(payload);
    },
  };
}

function loadFailover(fetchImpl) {
  const code = readFileSync(new URL("../api-failover.js", import.meta.url), "utf8");
  const context = {
    AbortController,
    fetch: fetchImpl,
    window: {
      clearTimeout,
      localStorage: makeStorage(),
      location: { origin: "https://picoin.science" },
      setTimeout,
    },
  };
  vm.runInNewContext(code, context, { filename: "api-failover.js" });
  return context.window.PicoinApiFailover;
}

const config = {
  apiBaseUrl: "/api/bootstrap",
  nodes: [
    { label: "mainnet-primary", url: "/api/bootstrap" },
    { label: "pool1-full-node", url: "/api/pool1-node" },
    { label: "retired-candidate", url: "/api/bootstrap-a", enabled: false },
  ],
};

{
  const calls = [];
  const failover = loadFailover(async (url) => {
    calls.push(url);
    if (String(url).startsWith("/api/bootstrap/")) {
      return jsonResponse(504, { detail: "primary slow" });
    }
    return jsonResponse(200, { status: "ok", url });
  });

  const client = failover.createClient({ config, defaultBaseUrl: "/api/bootstrap", storageKey: "test" });
  const first = await client.fetchJson("/health");
  assert.equal(first.baseUrl, "/api/pool1-node");
  assert.equal(first.payload.status, "ok");
  assert.deepEqual(calls, ["/api/bootstrap/health", "/api/pool1-node/health"]);

  calls.length = 0;
  const second = await client.fetchJson("/protocol");
  assert.equal(second.baseUrl, "/api/pool1-node");
  assert.deepEqual(calls, ["/api/pool1-node/protocol"]);
}

{
  const calls = [];
  const failover = loadFailover(async (url) => {
    calls.push(url);
    return jsonResponse(504, { detail: "primary slow" });
  });

  const client = failover.createClient({ config, defaultBaseUrl: "/api/bootstrap", storageKey: "test-post" });
  await assert.rejects(
    () => client.fetchJson("/tx/send", { method: "POST", body: "{}" }),
    /All bootstrap endpoints failed/,
  );
  assert.deepEqual(calls, ["/api/bootstrap/tx/send"]);
}

console.log("phase2 failover tests passed");
