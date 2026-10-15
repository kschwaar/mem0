import { SpanStatusCode } from "@opentelemetry/api";
import { InMemorySpanExporter as mockInMemorySpanExporter } from "@opentelemetry/sdk-trace-base";
import {
  configureRuntimeTracing,
  flushRuntimeTracing,
  shutdownRuntimeTracing,
  traceRuntimeObject,
  withRuntimeSpan,
} from "./runtime_tracing";

const mockExporters: mockInMemorySpanExporter[] = [];
// Only the network exporter is replaced; provider, processor and async context are real OTEL.
jest.mock("@opentelemetry/exporter-trace-otlp-http", () => ({
  OTLPTraceExporter: class extends mockInMemorySpanExporter {
    constructor() {
      super();
      mockExporters.push(this);
    }
  },
}));

async function finishedSpans() {
  await flushRuntimeTracing();
  return mockExporters[mockExporters.length - 1].getFinishedSpans();
}

describe("runtime inference boundaries", () => {
  beforeEach(() => {
    mockExporters.length = 0;
    process.env.MEM0_RUNTIME_TRACING = "true";
    process.env.LANGFUSE_PUBLIC_KEY = "test-public";
    process.env.LANGFUSE_SECRET_KEY = "test-secret";
  });
  afterEach(async () => {
    await shutdownRuntimeTracing();
    delete process.env.MEM0_RUNTIME_TRACING;
    delete process.env.LANGFUSE_PUBLIC_KEY;
    delete process.env.LANGFUSE_SECRET_KEY;
  });

  test("concurrent inference creates distinct children under a request root, not duplicate SDK generations", async () => {
    await configureRuntimeTracing();
    const result = {
      model: "local-model",
      usage: { prompt_tokens: 3, completion_tokens: 2 },
      choices: [{ text: "private-output" }],
    };
    const client = { chat: { completions: { create: async () => result } } };
    const provider = traceRuntimeObject(
      {
        client,
        model: "requested-model",
        generateResponse: async () => {
          await Promise.resolve();
          return client.chat.completions.create();
        },
      },
      "test",
    );
    await withRuntimeSpan("request", "span", undefined, async () => {
      const responses = await Promise.all([
        provider.generateResponse(),
        provider.generateResponse(),
      ]);
      expect(responses).toEqual([result, result]);
      expect(responses[0]).toBe(result);
    });
    const spans = await finishedSpans();
    const root = spans.find((span) => span.name === "request")!;
    const children = spans.filter(
      (span) => span.attributes["langfuse.observation.type"] === "generation",
    );
    expect(children).toHaveLength(2);
    expect(
      new Set(children.map((span) => span.spanContext().spanId)).size,
    ).toBe(2);
    for (const span of children) {
      expect(span.parentSpanContext?.spanId).toBe(root.spanContext().spanId);
      expect(span.spanContext().traceId).toBe(root.spanContext().traceId);
      expect(span.attributes["langfuse.observation.model.name"]).toBe(
        "local-model",
      );
      expect(
        JSON.parse(
          String(span.attributes["langfuse.observation.usage_details"]),
        ),
      ).toEqual({ input: 3, output: 2 });
    }
    expect(JSON.stringify(spans.map((span) => span.attributes))).not.toContain(
      "private-output",
    );
  });

  test("direct local embedding provider imports are observed without an SDK client", async () => {
    await configureRuntimeTracing();
    const vector = [0.25, 0.75];
    const provider = traceRuntimeObject(
      { model: "local-embedder", embed: async () => vector },
      "local",
    );
    expect(await provider.embed()).toBe(vector);
    const spans = await finishedSpans();
    expect(
      spans.map((span) => span.attributes["langfuse.observation.type"]),
    ).toEqual(["embedding"]);
    expect(spans[0].attributes["langfuse.observation.model.name"]).toBe(
      "local-embedder",
    );
    expect(Object.values(spans[0].attributes)).not.toContain(vector);
  });

  test("provider failures retain exception identity and do not export exception text", async () => {
    await configureRuntimeTracing();
    const failure = new Error("private-provider-error");
    let calls = 0;
    const provider = traceRuntimeObject(
      {
        generateResponse: async () => {
          calls++;
          throw failure;
        },
      },
      "failing",
    );
    await expect(provider.generateResponse()).rejects.toBe(failure);
    expect(calls).toBe(1);
    const spans = await finishedSpans();
    expect(spans[0].status.code).toBe(SpanStatusCode.ERROR);
    expect(spans[0].events).toEqual([]);
    expect(JSON.stringify(spans[0].attributes)).not.toContain(failure.message);
  });

  test("stream spans end after consumption and preserve frozen response fields", async () => {
    await configureRuntimeTracing();
    const chunks = [
      { type: "text-delta", textDelta: "private-stream-text" },
      { type: "finish", usage: { inputTokens: 4, outputTokens: 1 } },
    ];
    const responsePrototype = {
      get kind() {
        return "provider-response";
      },
    };
    const marker = Symbol("marker");
    const provider = traceRuntimeObject(
      {
        modelId: "stream-model",
        doStream: async () => {
          const response = Object.create(responsePrototype);
          Object.defineProperties(response, {
            headers: { value: { retained: "yes" }, enumerable: true },
            [marker]: { value: "hidden", enumerable: false },
            stream: {
              value: new ReadableStream<unknown>({
                start(controller) {
                  for (const chunk of chunks) controller.enqueue(chunk);
                  controller.close();
                },
              }),
              enumerable: true,
            },
          });
          return Object.freeze(response);
        },
      },
      "streaming",
    );
    const response = await provider.doStream();
    expect(response.headers).toEqual({ retained: "yes" });
    expect(response.kind).toBe("provider-response");
    expect(response[marker]).toBe("hidden");
    expect(await finishedSpans()).toEqual([]);
    const reader = response.stream.getReader();
    const received: unknown[] = [];
    for (;;) {
      const next = await reader.read();
      if (next.done) break;
      received.push(next.value);
    }
    expect(received).toEqual(chunks);
    const spans = await finishedSpans();
    expect(spans).toHaveLength(1);
    expect(
      JSON.parse(
        String(spans[0].attributes["langfuse.observation.usage_details"]),
      ),
    ).toEqual({ input: 4, output: 1 });
    expect(JSON.stringify(spans[0].attributes)).not.toContain(
      "private-stream-text",
    );
  });

  test("configuration is single-flight and shutdown permits a fresh owned exporter", async () => {
    await Promise.all([
      configureRuntimeTracing(),
      configureRuntimeTracing(),
      configureRuntimeTracing(),
    ]);
    expect(mockExporters).toHaveLength(1);
    await shutdownRuntimeTracing();
    await configureRuntimeTracing();
    await withRuntimeSpan("fresh-lifecycle", "span", undefined, () => "value");
    expect(mockExporters).toHaveLength(2);
    expect((await finishedSpans()).map((span) => span.name)).toEqual([
      "fresh-lifecycle",
    ]);
  });

  test("explicit nonlocal endpoints fail before creating an exporter", async () => {
    await expect(
      configureRuntimeTracing({ baseUrl: "https://example.com" }),
    ).rejects.toThrow("valid local Langfuse");
    expect(mockExporters).toEqual([]);
  });
});
