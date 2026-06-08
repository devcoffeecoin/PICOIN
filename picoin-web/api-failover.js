(function () {
  function cleanUrl(value) {
    return String(value || "").replace(/\/+$/, "");
  }

  function safePath(path) {
    const value = String(path || "");
    return value.startsWith("/") ? value : `/${value}`;
  }

  function storageAvailable() {
    try {
      return typeof window.localStorage !== "undefined";
    } catch (_error) {
      return false;
    }
  }

  function uniqueNodes(nodes) {
    const seen = new Set();
    return nodes
      .filter((node) => node && node.enabled !== false)
      .map((node, index) => ({
        label: node.label || `Bootstrap ${index + 1}`,
        url: cleanUrl(node.url),
      }))
      .filter((node) => {
        if (!node.url || seen.has(node.url)) return false;
        seen.add(node.url);
        return true;
      });
  }

  function normalizeNodes(config, defaultBaseUrl) {
    const primaryUrl = cleanUrl(config.apiBaseUrl || defaultBaseUrl || window.location.origin);
    const configured = Array.isArray(config.nodes) ? config.nodes : [];
    const nodes = uniqueNodes([
      { label: config.primaryLabel || "Primary", url: primaryUrl },
      ...configured,
    ]);
    return nodes.length ? nodes : [{ label: "Primary", url: primaryUrl }];
  }

  function createClient(options = {}) {
    const config = options.config || {};
    const nodes = normalizeNodes(config, options.defaultBaseUrl);
    const timeoutMs = Number(options.timeoutMs || config.timeoutMs || 12000);
    const storageKey = options.storageKey || "";
    let activeUrl = nodes[0].url;

    if (storageKey && storageAvailable()) {
      const stored = window.localStorage.getItem(storageKey);
      if (nodes.some((node) => node.url === stored)) {
        activeUrl = stored;
      }
    }

    function remember(url) {
      activeUrl = cleanUrl(url) || nodes[0].url;
      if (storageKey && storageAvailable()) {
        window.localStorage.setItem(storageKey, activeUrl);
      }
    }

    function orderedNodes(allowFailover) {
      if (!allowFailover) return [nodes[0]];
      const active = nodes.find((node) => node.url === activeUrl);
      return active ? [active, ...nodes.filter((node) => node.url !== active.url)] : nodes;
    }

    async function requestFrom(node, path, options = {}) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), Number(options.timeoutMs || timeoutMs));
      const fetchOptions = { ...options };
      delete fetchOptions.timeoutMs;
      delete fetchOptions.allowFailover;
      delete fetchOptions.allowFailoverWrites;
      delete fetchOptions.failoverLabel;
      fetchOptions.signal = controller.signal;
      fetchOptions.mode = fetchOptions.mode || "cors";
      fetchOptions.headers = {
        Accept: "application/json",
        ...(fetchOptions.headers || {}),
      };

      try {
        const response = await fetch(`${node.url}${safePath(path)}`, fetchOptions);
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
          throw new Error(payload.detail || response.statusText || `Error ${response.status}`);
        }
        return payload;
      } catch (error) {
        if (error.name === "AbortError") {
          throw new Error(`Timeout after ${Number(options.timeoutMs || timeoutMs) / 1000}s`);
        }
        throw error;
      } finally {
        window.clearTimeout(timeout);
      }
    }

    async function fetchJson(path, options = {}) {
      const method = String(options.method || "GET").toUpperCase();
      const allowFailover =
        options.allowFailover !== undefined
          ? Boolean(options.allowFailover)
          : method === "GET" || method === "HEAD" || Boolean(options.allowFailoverWrites);
      const errors = [];

      for (const node of orderedNodes(allowFailover)) {
        try {
          const payload = await requestFrom(node, path, options);
          remember(node.url);
          return { payload, baseUrl: node.url, label: node.label };
        } catch (error) {
          errors.push(`${node.label}: ${error.message || String(error)}`);
        }
      }

      throw new Error(`All bootstrap endpoints failed for ${safePath(path)}: ${errors.join(" | ")}`);
    }

    return {
      activeBaseUrl: () => activeUrl,
      endpoints: () => nodes.slice(),
      fetchJson,
    };
  }

  window.PicoinApiFailover = {
    cleanUrl,
    createClient,
    normalizeNodes,
  };
})();
