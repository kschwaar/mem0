/** Coordinator-only synthetic ingestion/readback; no model calls or credential files. */
import { grade, type EvalCase } from "./evaluate_ai_calls";
import { randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { EvaluationTracing, tracingConfigFromEnv } from "./evaluation_tracing";
import { createScoreOutbox, ScoreOutcome } from "./evaluation_scores";

export async function smokeLangfuse(args: string[]): Promise<number> {
  if (args.length !== 2 && args.length !== 4)
    throw new Error("INVALID_ARGUMENTS");
  if (
    args[0] !== "--output-dir" ||
    !args[1] ||
    (args.length === 4 && args[2] !== "--verify-timeout-ms")
  )
    throw new Error("INVALID_ARGUMENTS");
  const timeout = args.length === 4 ? Number(args[3]) : 120000;
  if (!Number.isInteger(timeout) || timeout < 1000 || timeout > 600000)
    throw new Error("INVALID_TIMEOUT");
  const config = tracingConfigFromEnv();
  const outputDir = resolve(args[1]);
  await mkdir(outputDir, { recursive: false });
  const healthResponse = await fetch(
    new URL("/api/public/health", config.baseUrl),
    { signal: AbortSignal.timeout(5000) },
  );
  if (!healthResponse.ok) throw new Error("HEALTH_FAILED");
  const health = (await healthResponse.json()) as { version?: string };
  const run_id = `synthetic-smoke-${randomUUID()}`;
  const started = new Date(Date.now() - 60000).toISOString();
  const tracing = new EvaluationTracing(config);
  const outbox = await createScoreOutbox(outputDir, config);
  const rows: (ScoreOutcome & { evidence: Record<string, unknown> })[] = [];
  const gradingCase: EvalCase = {
    id: "synthetic-schema",
    task: "llm",
    input: {},
    expect: { additive_schema: true },
  };
  for (const scenario of [
    {
      case_id: "success",
      fallback: false,
      output: '{"memory":[]}',
    },
    {
      case_id: "schema_failure",
      fallback: false,
      output: "{malformed",
    },
    {
      case_id: "fallback",
      fallback: true,
      output: '{"memory":[]}',
    },
  ]) {
    const failures = grade(gradingCase, scenario.output);
    if (scenario.fallback) failures.push("provider_fallback");
    const item = {
      ...scenario,
      failures,
      status: failures.length ? ("fail" as const) : ("pass" as const),
    };
    const root = tracing.start(
      "mem0.synthetic.smoke",
      "span",
      {
        run_id,
        case_id: item.case_id,
        repeat: 0,
        mode: "offline_synthetic",
        synthetic: true,
      },
      true,
    );
    await root.run(async () => {
      const fixture = tracing.start("synthetic.fixture", "span");
      tracing.content(fixture, { fixture: item.case_id }, item.output);
      fixture.set({ "langfuse.observation.metadata.synthetic": true });
      fixture.end();
      const grader = tracing.start("synthetic.grade", "evaluator");
      grader.set({
        "langfuse.observation.metadata.failures": JSON.stringify(item.failures),
        "langfuse.observation.metadata.fallback_detected": item.fallback,
      });
      grader.end(item.status);
    });
    root.end(item.status);
    const row = {
      run_id,
      call_id: randomUUID(),
      case_id: item.case_id,
      repeat: 0,
      mode: "offline_synthetic" as const,
      status: item.status,
      fallback_detected: item.fallback,
      semantic_failures: item.failures,
      trace_id: root.span!.spanContext().traceId,
      root_observation_id: root.span!.spanContext().spanId,
      evidence: {
        synthetic: true,
        model_calls: 0,
        scenario: item.case_id,
        failures: item.failures,
        fallback_injected: item.fallback,
      },
    };
    rows.push(row);
    await outbox.enqueue(row);
  }
  await writeFile(
    resolve(outputDir, "synthetic-evidence.json"),
    JSON.stringify(
      {
        run_id,
        model_calls: 0,
        mode: "offline_synthetic",
        health_version: health.version ?? null,
        rows,
      },
      null,
      2,
    ),
  );
  const traces = await tracing.shutdown();
  const scores = await outbox.flush();
  const journal = (
    await readFile(resolve(outputDir, "scores-outbox.jsonl"), "utf8")
  )
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  const expectedScores = journal
    .filter((entry) => entry.type === "score")
    .map((entry) => entry.score);
  const authorization = `Basic ${Buffer.from(`${config.publicKey}:${config.secretKey}`).toString("base64")}`;
  async function getRows(
    path: string,
    query: Record<string, string>,
  ): Promise<any[]> {
    const url = new URL(path, config.baseUrl);
    url.search = new URLSearchParams(query).toString();
    const response = await fetch(url, {
      headers: { Authorization: authorization },
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error(`READBACK_HTTP_${response.status}`);
    const body = (await response.json()) as { data?: unknown };
    if (!Array.isArray(body.data)) throw new Error("INVALID_READBACK_SCHEMA");
    return body.data;
  }
  const end = Date.now() + timeout;
  let observed = 0,
    attached = 0;
  let readback_error: string | null = null;
  try {
    while (Date.now() < end) {
      observed = 0;
      for (const row of rows) {
        const observations = await getRows("/api/public/v2/observations", {
          traceId: row.trace_id!,
          fields: "core,basic,metadata,usage",
          limit: "100",
          fromStartTime: started,
          toStartTime: new Date(Date.now() + 60000).toISOString(),
        });
        if (observations.length !== 3) continue;
        const root = observations.find((o) => o.id === row.root_observation_id);
        if (!root || root.traceId !== row.trace_id || root.parentObservationId)
          throw new Error("INVALID_ROOT_LINKAGE");
        for (const observation of observations) {
          if (
            observation.traceId !== row.trace_id ||
            (observation.id !== root.id &&
              observation.parentObservationId !== root.id)
          )
            throw new Error("INVALID_CHILD_LINKAGE");
          if (
            ["GENERATION", "EMBEDDING"].includes(
              String(observation.type).toUpperCase(),
            )
          )
            throw new Error("FABRICATED_MODEL_OBSERVATION");
          if (
            observation.metadata?.mode !== "offline_synthetic" ||
            observation.metadata?.case_id !== row.case_id
          )
            throw new Error("MISSING_SYNTHETIC_METADATA");
        }
        observed += observations.length;
      }
      const readScores = await getRows("/api/public/v3/scores", {
        id: expectedScores.map((s) => s.id).join(","),
        fields: "subject,details",
        limit: "100",
      });
      attached = 0;
      for (const expected of expectedScores) {
        const score = readScores.find((s) => s.id === expected.id);
        if (!score) continue;
        const expectedValue =
          expected.dataType === "BOOLEAN"
            ? Boolean(expected.value)
            : expected.value;
        if (
          score.comment !== "offline_synthetic" ||
          score.dataType !== expected.dataType ||
          score.value !== expectedValue ||
          score.name !== expected.name ||
          score.subject?.kind !== "observation" ||
          score.subject?.id !== expected.observationId ||
          score.subject?.traceId !== expected.traceId
        )
          throw new Error("INVALID_SCORE_LINKAGE_OR_VALUE");
        attached++;
      }
      if (observed === 9 && attached === expectedScores.length) break;
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    readback_error =
      /^(READBACK_HTTP_\d{3}|INVALID_READBACK_SCHEMA|INVALID_ROOT_LINKAGE|INVALID_CHILD_LINKAGE|FABRICATED_MODEL_OBSERVATION|MISSING_SYNTHETIC_METADATA|INVALID_SCORE_LINKAGE_OR_VALUE)$/.test(
        message,
      )
        ? message
        : "READBACK_FAILED";
  }
  const verified =
    readback_error === null &&
    observed === 9 &&
    attached === 9 &&
    traces.exported === 9 &&
    scores.pending === 0;
  const report = {
    run_id,
    synthetic: true,
    model_calls: 0,
    traces,
    scores,
    readback_error,
    observations_verified: observed,
    scores_verified: attached,
    verified,
  };
  await writeFile(
    resolve(outputDir, "smoke-report.json"),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report));
  return verified ? 0 : 1;
}
if (/smoke_langfuse\.(?:ts|js)$/.test(process.argv[1] ?? "")) {
  void smokeLangfuse(process.argv.slice(2))
    .then((code) => {
      process.exitCode = code;
    })
    .catch(() => {
      console.error(
        "SYNTHETIC_SMOKE_FAILED; inspect safe local evidence and instance API schema",
      );
      process.exitCode = 1;
    });
}
