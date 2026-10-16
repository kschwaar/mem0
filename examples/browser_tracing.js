const DEFAULT_ENDPOINT = "http://localhost:4318/v1/traces";
let tracingConfig = { enabled: false, endpoint: DEFAULT_ENDPOINT };

function isLoopback(hostname) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return host === "localhost" || host === "::1" || host === "127.0.0.1" || /^127(?:\.\d{1,3}){3}$/.test(host);
}

function safeEndpoint(endpoint) {
  try {
    const url = new URL(endpoint || DEFAULT_ENDPOINT);
    if (!isLoopback(url.hostname) || !["http:", "https:"].includes(url.protocol) || url.username || url.password
        || url.pathname !== "/v1/traces" || url.search || url.hash) {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}

function randomHex(byteLength) {
  const bytes = new Uint8Array(byteLength);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}


function stringAttribute(key, value) {
  return { key, value: { stringValue: String(value) } };
}

export function configureBrowserTracing({ enabled = false, endpoint = DEFAULT_ENDPOINT } = {}) {
  const safe = safeEndpoint(endpoint);
  tracingConfig = { enabled: enabled === true && Boolean(safe), endpoint: safe || DEFAULT_ENDPOINT };
  return tracingConfig;
}

export async function traceBrowserInference({ provider, model, operation }, operationFn) {
  const config = tracingConfig;
  if (!config.enabled) return operationFn();

  let traceId;
  let spanId;
  let startTimeUnixNano;
  try {
    traceId = randomHex(16);
    spanId = randomHex(8);
    startTimeUnixNano = (BigInt(Date.now()) * 1000000n).toString();
  } catch {
    return operationFn();
  }
  let outcome = "success";
  try {
    return await operationFn();
  } catch (error) {
    outcome = error?.name === "AbortError" ? "cancelled" : "error";
    throw error;
  } finally {
    let endTimeUnixNano = startTimeUnixNano;
    try { endTimeUnixNano = (BigInt(Date.now()) * 1000000n).toString(); } catch { /* Preserve the inference outcome if timestamping fails. */ }
    const span = {
      traceId,
      spanId,
      name: String(operation || "chat") + " " + String(model || "unknown"),
      kind: 3,
      startTimeUnixNano,
      endTimeUnixNano,
      attributes: [
        stringAttribute("langfuse.observation.type", "generation"),
        stringAttribute("gen_ai.operation.name", operation || "chat"),
        stringAttribute("gen_ai.provider.name", provider || "unknown"),
        stringAttribute("langfuse.observation.model.name", model || "unknown"),
        stringAttribute("mem0.outcome", outcome),
      ],
      status: outcome === "success" ? { code: 1 } : { code: 2 },
    };
    try {
      await fetch(config.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(5000),
        body: JSON.stringify({
          resourceSpans: [{
            resource: { attributes: [stringAttribute("service.name", "mem0-browser-example")] },
            scopeSpans: [{ scope: { name: "mem0.browser.tracing" }, spans: [span] }],
          }],
        }),
      });
    } catch {
      // Export is best effort and must not change the inference result.
    }
  }
}
