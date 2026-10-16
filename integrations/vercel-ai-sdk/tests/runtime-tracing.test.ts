import { trace, type Span, type Tracer } from "@opentelemetry/api";

import { withProviderGeneration } from "../src/runtime-tracing";

it("does not retry a failed provider when no recording tracer is installed", async () => {
  const failure = new Error("provider failure");
  let billedRequests = 0;
  await expect(
    withProviderGeneration("openai", "test-model", "generate", async () => {
      billedRequests++;
      throw failure;
    }),
  ).rejects.toBe(failure);
  expect(billedRequests).toBe(1);
});

it("keeps frozen streamed response metadata and ends tracing only after consumption", async () => {
  const ended: boolean[] = [];
  const span = {
    isRecording: () => true,
    spanContext: () => ({
      traceId: "1".repeat(32),
      spanId: "2".repeat(16),
      traceFlags: 1,
    }),
    setAttribute: () => span,
    setStatus: () => span,
    end: () => {
      ended.push(true);
    },
  } as unknown as Span;
  const getTracer = jest.spyOn(trace, "getTracer").mockReturnValue({
    startSpan: () => span,
  } as unknown as Tracer);
  const marker = Symbol("provider-metadata");
  const prototype = {
    get providerKind() {
      return "local-provider";
    },
  };
  const response = Object.freeze(
    Object.create(prototype, {
      stream: {
        value: new ReadableStream({
          start(controller) {
            controller.enqueue({ type: "text-delta", text: "hello" });
            controller.close();
          },
        }),
        enumerable: true,
      },
      [marker]: { value: "retained", enumerable: false },
    }),
  );
  try {
    const result = await withProviderGeneration(
      "openai",
      "local-model",
      "stream",
      async () => response,
    );
    expect(result.providerKind).toBe("local-provider");
    expect(result[marker]).toBe("retained");
    expect(ended).toEqual([]);
    const reader = result.stream.getReader();
    expect(await reader.read()).toEqual({
      done: false,
      value: { type: "text-delta", text: "hello" },
    });
    expect(await reader.read()).toEqual({ done: true, value: undefined });
    expect(ended).toEqual([true]);
  } finally {
    getTracer.mockRestore();
  }
});
