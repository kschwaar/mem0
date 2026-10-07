/** Real HTTP ingestion boundaries; all credentials and provider replies are fake. */
import { createServer, Server } from "node:http";
import { AddressInfo } from "node:net";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { EvalCase, runEvaluation } from "../scripts/evaluate_ai_calls";
import { smokeLangfuse } from "../scripts/smoke_langfuse";
import { EvaluationTracing } from "../scripts/evaluation_tracing";
import { createScoreOutbox } from "../scripts/evaluation_scores";

interface RequestRecord {
  path: string;
  headers: Record<string, unknown>;
  body: any;
}
let server: Server;
let baseUrl: string;
let directory: string;
let requests: RequestRecord[];
let responseStatus: number;
let corruptScoreReadback = false;
beforeEach(async () => {
  directory = await mkdtemp(join(tmpdir(), "mem0-langfuse-http-"));
  requests = [];
  responseStatus = 200;
  corruptScoreReadback = false;
  server = createServer(async (request, response) => {
    if (request.method === "GET") {
      const url = new URL(request.url!, "http://fake.local");
      let body: any = { version: "4.53.0" };
      if (url.pathname === "/api/public/v2/observations")
        body = {
          data: exportedSpans()
            .filter((span) => span.traceId === url.searchParams.get("traceId"))
            .map((span) => ({
              id: span.spanId,
              traceId: span.traceId,
              parentObservationId: span.parentSpanId || null,
              type: attributes(span)["langfuse.observation.type"],
              metadata: Object.fromEntries(
                Object.entries(attributes(span))
                  .filter(([key]) =>
                    key.startsWith("langfuse.observation.metadata."),
                  )
                  .map(([key, value]) => [
                    key.slice("langfuse.observation.metadata.".length),
                    value,
                  ]),
              ),
            })),
          meta: {},
        };
      if (url.pathname === "/api/public/v3/scores")
        body = {
          data: requests
            .filter((r) => r.path === "/api/public/scores")
            .map((r) => ({
              ...r.body,
              value:
                r.body.dataType === "BOOLEAN"
                  ? corruptScoreReadback
                    ? r.body.value
                    : Boolean(r.body.value)
                  : r.body.value,
              subject: {
                kind: "observation",
                id: r.body.observationId,
                traceId: r.body.traceId,
              },
            })),
          meta: {},
        };
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify(body));
      return;
    }
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    requests.push({
      path: request.url!,
      headers: request.headers,
      body: JSON.parse(Buffer.concat(chunks).toString()),
    });
    response.writeHead(responseStatus, { "content-type": "application/json" });
    response.end(
      JSON.stringify(
        request.url === "/api/public/scores"
          ? { id: requests.at(-1)!.body.id }
          : {},
      ),
    );
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterEach(async () => {
  if (!server?.listening) {
    await rm(directory, { recursive: true, force: true });
    return;
  }
  server.closeAllConnections();
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
  await rm(directory, { recursive: true, force: true });
});
const config = () => ({
  baseUrl,
  publicKey: "fake-public",
  secretKey: "fake-secret",
  timeoutMs: 200,
  maxAttempts: 1,
});
const outcome = {
  run_id: "synthetic-http-test",
  call_id: "call",
  case_id: "case",
  repeat: 0,
  mode: "live" as const,
  status: "pass" as const,
  trace_id: "a".repeat(32),
  root_observation_id: "b".repeat(16),
  fallback_detected: false,
};

test("Scores API attaches actual IDs, types values, deduplicates and replays a failed delivery", async () => {
  const outbox = await createScoreOutbox(directory, config());
  await outbox.enqueue(outcome);
  await outbox.enqueue(outcome);
  expect(requests).toHaveLength(0); // Enqueue stays outside network delivery.
  responseStatus = 503;
  const failed = await outbox.flush();
  expect(failed.pending).toBe(3);
  expect(failed.delivered).toBe(0);
  expect(failed.failedAttempts).toBeGreaterThan(0);
  const firstIds = requests.map((r) => r.body.id).sort();
  requests = [];
  responseStatus = 200;
  const replay = await createScoreOutbox(directory, config());
  const delivered = await replay.flush();
  expect(delivered.pending).toBe(0);
  expect(delivered.delivered).toBe(3);
  expect(requests.map((r) => r.body.id).sort()).toEqual(firstIds);
  for (const request of requests) {
    expect(request.path).toBe("/api/public/scores");
    expect(request.body).toMatchObject({
      traceId: outcome.trace_id,
      observationId: outcome.root_observation_id,
    });
    expect(request.headers.authorization).toBe(
      `Basic ${Buffer.from("fake-public:fake-secret").toString("base64")}`,
    );
  }
  expect(
    requests.map((r) => [r.body.name, r.body.dataType, r.body.value]),
  ).toEqual(
    expect.arrayContaining([
      ["contract_pass", "BOOLEAN", 1],
      ["trial_status", "CATEGORICAL", "pass"],
      ["fallback_detected", "BOOLEAN", 0],
    ]),
  );
  requests = [];
  await replay.enqueue(outcome);
  await replay.flush();
  expect(requests).toHaveLength(0);
});

test("skipped or unknown outcomes never manufacture boolean score evidence", async () => {
  const outbox = await createScoreOutbox(directory, config());
  await outbox.enqueue({ ...outcome, status: "skip", fallback_detected: null });
  await outbox.enqueue({
    ...outcome,
    call_id: "unknown",
    status: "error",
    fallback_detected: null,
  });
  await outbox.flush();
  expect(
    requests.filter((r) => r.body.name === "fallback_detected"),
  ).toHaveLength(0);
  expect(
    requests.filter(
      (r) =>
        r.body.name === "contract_pass" && r.body.traceId === outcome.trace_id,
    ),
  ).toHaveLength(0);
  expect(requests.find((r) => r.body.value === "skip")?.body.dataType).toBe(
    "CATEGORICAL",
  );
});

function exportedSpans(): any[] {
  return requests
    .filter((r) => r.path === "/api/public/otel/v1/traces")
    .flatMap((r) =>
      r.body.resourceSpans.flatMap((resource: any) =>
        resource.scopeSpans.flatMap((scope: any) => scope.spans),
      ),
    );
}
function attributes(span: any): Record<string, any> {
  return Object.fromEntries(
    span.attributes.map((a: any) => [
      a.key,
      a.value.stringValue ??
        a.value.intValue ??
        a.value.doubleValue ??
        a.value.boolValue,
    ]),
  );
}

test("real OTLP HTTP export preserves concurrent trace trees, SDK-only usage and content privacy", async () => {
  const tracing = new EvaluationTracing({ ...config(), timeoutMs: 2000 });
  const links = await Promise.all(
    ["slow", "fast"].map(async (case_id) => {
      const root = tracing.start(
        "trial",
        "span",
        { run_id: "http-run", case_id, repeat: 0, mode: "live" },
        true,
      );
      await root.run(async () => {
        const operation = tracing.start("adapter", "span");
        await operation.run(async () => {
          const sdk = tracing.start("chat.completions.create", "generation");
          await sdk.run(async () => {
            await new Promise((resolve) =>
              setTimeout(resolve, case_id === "slow" ? 20 : 1),
            );
            tracing.response(sdk, {
              model: "fake-model",
              usage: {
                prompt_tokens: case_id === "slow" ? 12 : 7,
                completion_tokens: 3,
                total_tokens: case_id === "slow" ? 15 : 10,
              },
            });
            tracing.content(
              sdk,
              { messages: [{ content: "PRIVATE_INPUT_SENTINEL" }] },
              "PRIVATE_OUTPUT_SENTINEL",
            );
          });
          sdk.end();
        });
        operation.end();
        const grader = tracing.start("grader", "evaluator");
        grader.end("pass");
      });
      root.end("pass");
      return {
        case_id,
        traceId: root.span!.spanContext().traceId,
        rootId: root.span!.spanContext().spanId,
      };
    }),
  );
  const report = await tracing.shutdown();
  expect(report).toMatchObject({
    created: 8,
    ended: 8,
    exported: 8,
    failed: 0,
    unknown: 0,
    timedOut: false,
  });
  const spans = exportedSpans();
  expect(spans).toHaveLength(8);
  expect(new Set(spans.map((s) => s.traceId)).size).toBe(2);
  for (const link of links) {
    expect(link.traceId).toMatch(/^[a-f0-9]{32}$/);
    expect(link.rootId).toMatch(/^[a-f0-9]{16}$/);
    const tree = spans.filter((s) => s.traceId === link.traceId);
    const root = tree.find((s) => s.spanId === link.rootId)!;
    const adapter = tree.find((s) => s.name === "adapter")!;
    const sdk = tree.find((s) => s.name === "chat.completions.create")!;
    const grader = tree.find((s) => s.name === "grader")!;
    expect(root.parentSpanId ?? "").toBe("");
    expect(adapter.parentSpanId).toBe(root.spanId);
    expect(sdk.parentSpanId).toBe(adapter.spanId);
    expect(grader.parentSpanId).toBe(root.spanId);
    for (const span of tree) {
      const a = attributes(span);
      expect(a["langfuse.observation.metadata.case_id"]).toBe(link.case_id);
      expect(a["langfuse.observation.metadata.run_id"]).toBe("http-run");
      expect(a["langfuse.observation.metadata.repeat"]).toBeDefined();
      if (span !== sdk)
        expect(a["langfuse.observation.usage_details"]).toBeUndefined();
      expect(a["langfuse.observation.input"]).toBeUndefined();
      expect(a["langfuse.observation.output"]).toBeUndefined();
    }
    const sdkAttributes = attributes(sdk);
    expect(
      JSON.parse(sdkAttributes["langfuse.observation.usage_details"]),
    ).toEqual({
      input: link.case_id === "slow" ? 12 : 7,
      output: 3,
      total: link.case_id === "slow" ? 15 : 10,
    });
    expect(sdkAttributes["langfuse.observation.metadata.input_sha256"]).toMatch(
      /^[a-f0-9]{64}$/,
    );
  }
  expect(JSON.stringify(requests)).not.toContain("PRIVATE_INPUT_SENTINEL");
  expect(JSON.stringify(requests)).not.toContain("PRIVATE_OUTPUT_SENTINEL");
  for (const request of requests) {
    expect(request.headers["x-langfuse-ingestion-version"]).toBe("4");
    expect(request.headers["content-type"]).toContain("application/json");
  }
});

test("failed OTLP ingestion accounts for failure without falsely claiming accepted spans", async () => {
  responseStatus = 400; // Nonretryable, fast failure.
  const tracing = new EvaluationTracing({ ...config(), timeoutMs: 1000 });
  const root = tracing.start(
    "trial",
    "span",
    { mode: "offline_synthetic" },
    true,
  );
  root.end("pass");
  const report = await tracing.shutdown();
  expect(report).toMatchObject({
    created: 1,
    ended: 1,
    exported: 0,
    failed: 1,
    unknown: 0,
  });
  expect(exportedSpans()).toHaveLength(1);
});

const fixture: EvalCase = {
  id: "privacy",
  task: "llm",
  input: {
    stage: "additive_extraction",
    prompt_args: {
      new_messages: "PRIVATE_INPUT_SENTINEL",
      existing_memories: [],
      current_date: "2026-10-06",
      timestamp: "2026-10-06",
    },
  },
  expect: { additive_schema: true },
  mock_output: '{"memory":[]}',
};
const fakeProviders = () => {
  const llm = {
    openai: {
      chat: {
        completions: {
          create: async () => ({
            model: "fake-model",
            usage: {
              prompt_tokens: 11,
              completion_tokens: 2,
              total_tokens: 13,
            },
            choices: [{ message: { content: fixture.mock_output } }],
          }),
        },
      },
    },
    generateChat: async () => ({ content: "", role: "assistant" }),
    generateResponse: async function () {
      await this.openai.chat.completions.create();
      return fixture.mock_output;
    },
  };
  return { llm };
};
test("evaluator retains result ledger and live gates when both trace and score export fail", async () => {
  const options = {
    offline: false,
    repeats: 2,
    concurrency: 2,
    config: {},
    minPassRate: 1,
  };
  const baseline = await runEvaluation(
    [fixture],
    { ...options, out: join(directory, "baseline") },
    fakeProviders(),
  );
  responseStatus = 400;
  const traced = await runEvaluation(
    [fixture],
    {
      ...options,
      out: join(directory, "traced"),
      traceBackend: "langfuse",
      traceConfig: { ...config(), timeoutMs: 1000 },
    },
    fakeProviders(),
  );
  for (const key of [
    "passed",
    "failed",
    "errors",
    "skipped",
    "measured",
    "pass_rate",
    "gates",
    "observed_sdk_calls",
  ])
    expect(traced[key]).toEqual(baseline[key]);
  const readLedger = async (run: string) =>
    (await readFile(join(directory, run, "calls.jsonl"), "utf8"))
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line));
  const plainRows = await readLedger("baseline");
  const tracedRows = await readLedger("traced");
  const resultProjection = (rows: any[]) =>
    rows
      .map((row) => ({
        case_id: row.case_id,
        repeat: row.repeat,
        status: row.status,
        request_sha256: row.request_sha256,
        response_sha256: row.response_sha256,
        semantic_failures: row.semantic_failures,
        fallback_detected: row.fallback_detected,
        usage: row.usage,
        provider_attempts: row.provider_attempts,
      }))
      .sort((a, b) => a.repeat - b.repeat);
  expect(resultProjection(tracedRows)).toEqual(resultProjection(plainRows));
  for (const row of tracedRows) {
    expect(row.trace_id).toMatch(/^[a-f0-9]{32}$/);
    expect(row.root_observation_id).toMatch(/^[a-f0-9]{16}$/);
    expect(row.content).toBeUndefined();
    expect(row.usage).toBeNull();
    expect(row.provider_attempts).toBeNull();
  }
  expect(traced.observability.traces.exported).toBe(0);
  expect(traced.observability.traces.failed).toBeGreaterThan(0);
  expect(traced.observability.scores.pending).toBeGreaterThan(0);
  const journal = await readFile(
    join(directory, "traced", "scores-outbox.jsonl"),
    "utf8",
  );
  expect(journal).not.toContain("fake-secret");
  expect(journal).not.toContain("PRIVATE_INPUT_SENTINEL");
  expect(
    requests
      .filter((r) => r.path === "/api/public/scores")
      .every((r) =>
        tracedRows.some(
          (row) =>
            row.trace_id === r.body.traceId &&
            row.root_observation_id === r.body.observationId,
        ),
      ),
  ).toBe(true);
});

test("synthetic smoke validates v4 row and Boolean/subject score schemas with zero model observations", async () => {
  const replaced = jest.replaceProperty(process, "env", {
    LANGFUSE_BASE_URL: baseUrl,
    LANGFUSE_PUBLIC_KEY: "fake-public",
    LANGFUSE_SECRET_KEY: "fake-secret",
  });
  const log = jest.spyOn(console, "log").mockImplementation(() => {});
  try {
    const output = join(directory, "smoke");
    expect(
      await smokeLangfuse([
        "--output-dir",
        output,
        "--verify-timeout-ms",
        "1000",
      ]),
    ).toBe(0);
    const report = JSON.parse(
      await readFile(join(output, "smoke-report.json"), "utf8"),
    );
    expect(report).toMatchObject({
      synthetic: true,
      model_calls: 0,
      observations_verified: 9,
      scores_verified: 9,
      verified: true,
    });
    expect(
      exportedSpans().some((s) =>
        ["generation", "embedding"].includes(
          attributes(s)["langfuse.observation.type"],
        ),
      ),
    ).toBe(false);
    const evidence = await readFile(
      join(output, "synthetic-evidence.json"),
      "utf8",
    );
    expect(evidence).toContain("invalid_json");
    expect(evidence).toContain("provider_fallback");
    expect(evidence).not.toContain("fake-secret");
  } finally {
    replaced.restore();
    log.mockRestore();
  }
});

test("synthetic smoke preserves evidence and rejects numeric Boolean v3 readback", async () => {
  corruptScoreReadback = true;
  const replaced = jest.replaceProperty(process, "env", {
    LANGFUSE_BASE_URL: baseUrl,
    LANGFUSE_PUBLIC_KEY: "fake-public",
    LANGFUSE_SECRET_KEY: "fake-secret",
  });
  const log = jest.spyOn(console, "log").mockImplementation(() => {});
  try {
    const output = join(directory, "smoke-invalid");
    expect(
      await smokeLangfuse([
        "--output-dir",
        output,
        "--verify-timeout-ms",
        "1000",
      ]),
    ).toBe(1);
    const report = JSON.parse(
      await readFile(join(output, "smoke-report.json"), "utf8"),
    );
    expect(report).toMatchObject({
      verified: false,
      readback_error: "INVALID_SCORE_LINKAGE_OR_VALUE",
      model_calls: 0,
    });
    expect(
      await readFile(join(output, "synthetic-evidence.json"), "utf8"),
    ).toContain("invalid_json");
  } finally {
    replaced.restore();
    log.mockRestore();
  }
});
