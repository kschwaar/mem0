import {
  InMemorySpanExporter,
  type SpanExporter,
} from "@opentelemetry/sdk-trace-base";
import { EvaluationTracing } from "../scripts/evaluation_tracing";
import { runEvaluation, type Providers } from "../scripts/evaluate_ai_calls";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
const config = {
  baseUrl: "http://127.0.0.1:1",
  publicKey: "test-public",
  secretKey: "test-secret",
  timeoutMs: 100,
};
// Capture before InMemorySpanExporter.shutdown clears spans.
function memory() {
  const exporter = new InMemorySpanExporter();
  const transport: SpanExporter = {
    export: (spans, cb) => exporter.export(spans, cb),
    shutdown: async () => {},
  };
  return { exporter, tracing: new EvaluationTracing(config, transport) };
}
test("Promise concurrency preserves real roots, SDK parentage, actual usage and privacy across repeated runs", async () => {
  const dir = await mkdtemp(join(tmpdir(), "tracing-"));
  try {
    for (let run = 0; run < 2; run++) {
      const { exporter, tracing } = memory();
      const client = {
        chat: {
          completions: {
            create: async (input: any) => {
              await new Promise((r) =>
                setTimeout(r, input.messages[0].content === "a" ? 12 : 2),
              );
              return {
                model: "served-model",
                usage: {
                  prompt_tokens: 3,
                  completion_tokens: 2,
                  total_tokens: 5,
                },
                choices: [{ message: { content: '{"ok":true}' } }],
              };
            },
          },
        },
      };
      const provider = {
        client,
        generateResponse: async (messages: any) =>
          (
            await client.chat.completions.create({
              messages,
              model: "requested-model",
            })
          ).choices[0].message.content,
      };
      const out = join(dir, String(run));
      const summary = await runEvaluation(
        ["a", "b"].map((id) => ({
          id,
          task: "llm" as const,
          input: { messages: [{ role: "user", content: id }] },
          expect: { json_required: true },
        })),
        {
          offline: false,
          repeats: 1,
          concurrency: 2,
          out,
          config: {},
          tracing,
        },
        { llm: provider } as unknown as Providers,
      );
      expect(summary.passed).toBe(2);
      expect(summary.observability.traces).toMatchObject({
        created: 8,
        ended: 8,
        exported: 8,
        failed: 0,
        unknown: 0,
      });
      const rows = (await readFile(join(out, "calls.jsonl"), "utf8"))
        .trim()
        .split("\n")
        .map((s) => JSON.parse(s));
      const spans = exporter.getFinishedSpans();
      expect(new Set(rows.map((r) => r.trace_id)).size).toBe(2);
      for (const row of rows) {
        expect(row.trace_id).toMatch(/^[0-9a-f]{32}$/);
        const group = spans.filter(
          (s) => s.spanContext().traceId === row.trace_id,
        );
        const root = group.find((s) => s.name === "mem0.eval.item")!;
        const op = group.find((s) => s.name === "mem0.eval.llm")!;
        const sdk = group.find((s) => s.name === "chat.completions.create")!;
        expect(root.spanContext().spanId).toBe(row.root_observation_id);
        expect(op.parentSpanContext?.spanId).toBe(root.spanContext().spanId);
        expect(sdk.parentSpanContext?.spanId).toBe(op.spanContext().spanId);
        expect(sdk.spanContext().spanId).toBe(row.sdk_spans[0].span_id);
        expect(sdk.attributes["langfuse.observation.usage_details"]).toBe(
          '{"input":3,"output":2,"total":5}',
        );
        expect(sdk.attributes["langfuse.observation.model.name"]).toBe(
          "served-model",
        );
        for (const span of group) {
          expect(span.attributes["langfuse.observation.metadata.case_id"]).toBe(
            row.case_id,
          );
          expect(span.attributes["langfuse.observation.input"]).toBeUndefined();
          expect(
            span.attributes["langfuse.observation.output"],
          ).toBeUndefined();
          if (span !== sdk)
            expect(
              span.attributes["gen_ai.usage.input_tokens"],
            ).toBeUndefined();
        }
      }
    }
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
test("export failures and hung shutdown have separate honest accounting", async () => {
  const failing: SpanExporter = {
    export: (_, cb) =>
      cb({ code: 1, error: new Error("credential must not surface") }),
    shutdown: async () => {},
  };
  const tracing = new EvaluationTracing(config, failing);
  tracing.start("root", "span", {}, true).end();
  expect(await tracing.shutdown()).toMatchObject({
    ended: 1,
    failed: 1,
    exported: 0,
    unknown: 0,
  });
  const hanging: SpanExporter = { export: () => {}, shutdown: async () => {} };
  const timed = new EvaluationTracing({ ...config, timeoutMs: 20 }, hanging);
  timed.start("root", "span", {}, true).end();
  expect(await timed.shutdown()).toMatchObject({
    timedOut: true,
    exported: 0,
    unknown: 1,
  });
});
test("content opt-in bounds and redacts content; Ollama usage maps only known fields", async () => {
  const exporter = new InMemorySpanExporter();
  const tracing = new EvaluationTracing(
    { ...config, recordContent: true },
    { export: (s, cb) => exporter.export(s, cb), shutdown: async () => {} },
  );
  const root = tracing.start(
    "root",
    "span",
    { secret: "hidden", case_id: "safe" },
    true,
  );
  tracing.content(
    root,
    { apiKey: "hidden", text: "x".repeat(20000), embedding: [1, 2] },
    { content: "approved" },
  );
  tracing.response(root, { model: "ollama", prompt_eval_count: 7 });
  root.end();
  await tracing.shutdown();
  const attrs = exporter.getFinishedSpans()[0].attributes;
  expect(JSON.stringify(attrs)).not.toContain("hidden");
  expect(
    String(attrs["langfuse.observation.input"]).length,
  ).toBeLessThanOrEqual(8192);
  expect(attrs["langfuse.observation.usage_details"]).toBe('{"input":7}');
  expect(attrs["gen_ai.usage.output_tokens"]).toBeUndefined();
});
