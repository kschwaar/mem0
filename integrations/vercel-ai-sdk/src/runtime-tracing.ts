import { context, SpanStatusCode, trace } from "@opentelemetry/api";
import type { Context, Span } from "@opentelemetry/api";
import { z } from "zod";

const tokenSchema = z.union([
  z.number().int().nonnegative(),
  z.object({ total: z.number().int().nonnegative().optional() }),
]);
const resultSchema = z.object({
  model: z.string().optional(),
  modelId: z.string().optional(),
  usage: z
    .object({
      inputTokens: tokenSchema.optional(),
      outputTokens: tokenSchema.optional(),
      promptTokens: z.number().int().nonnegative().optional(),
      completionTokens: z.number().int().nonnegative().optional(),
      input_tokens: z.number().int().nonnegative().optional(),
      output_tokens: z.number().int().nonnegative().optional(),
    })
    .optional(),
});

function recordResult(span: Span, result: unknown): void {
  try {
    const parsed = resultSchema.safeParse(result);
    if (!parsed.success) return;
    const { model, modelId, usage } = parsed.data;
    if (model ?? modelId)
      span.setAttribute(
        "langfuse.observation.model.name",
        (model ?? modelId)!.slice(0, 512),
      );
    const input =
      typeof usage?.inputTokens === "number"
        ? usage.inputTokens
        : usage?.inputTokens?.total;
    const output =
      typeof usage?.outputTokens === "number"
        ? usage.outputTokens
        : usage?.outputTokens?.total;
    const details: Record<string, number> = {};
    const inputCount = input ?? usage?.promptTokens ?? usage?.input_tokens;
    const outputCount =
      output ?? usage?.completionTokens ?? usage?.output_tokens;
    if (inputCount !== undefined) details.input = inputCount;
    if (outputCount !== undefined) details.output = outputCount;
    if (Object.keys(details).length)
      span.setAttribute(
        "langfuse.observation.usage_details",
        JSON.stringify(details),
      );
  } catch {
    /* Instrumentation must not replace provider behavior. */
  }
}

function field(value: unknown, name: string): unknown {
  try {
    return typeof value === "object" && value !== null
      ? Reflect.get(value, name)
      : undefined;
  } catch {
    return undefined;
  }
}
function replaceStream<T extends object>(
  result: T,
  stream: ReadableStream<unknown>,
): T {
  const descriptors: PropertyDescriptorMap =
    Object.getOwnPropertyDescriptors(result);
  const descriptor = descriptors.stream;
  descriptors.stream = {
    value: stream,
    enumerable: descriptor?.enumerable ?? true,
    configurable: descriptor?.configurable ?? true,
    writable: "writable" in (descriptor ?? {}) ? descriptor.writable : true,
  };
  return Object.defineProperties(
    Object.create(Object.getPrototypeOf(result)),
    descriptors,
  ) as T;
}

/** One dispatch observation; returned streams own completion, error and cancellation. */
export async function withProviderGeneration<T extends object>(
  provider: string,
  model: string,
  operation: "generate" | "stream",
  run: (span?: Span) => PromiseLike<T>,
): Promise<T> {
  const tracer = trace.getTracer("mem0.vercel-ai-provider", "1");
  let span: Span;
  let active: Context;
  try {
    span = tracer.startSpan(`mem0.generation.${provider}.${operation}`, {
      attributes: {
        "langfuse.observation.type": "generation",
        "langfuse.observation.model.name": model.slice(0, 512),
        "gen_ai.provider.name": provider,
        "mem0.attempt_scope": "logical_provider_invocation",
      },
    });
    active = trace.setSpan(context.active(), span);
  } catch {
    return run();
  }
  if (!span.isRecording()) return await run();
  let streamOwnsSpan = false;
  try {
    const result = await context.with(active, () => run(span));
    if (operation === "generate") {
      recordResult(span, result);
      return result;
    }
    const source = field(result, "stream");
    if (
      typeof ReadableStream === "undefined" ||
      !(source instanceof ReadableStream) ||
      source.locked
    )
      return result;
    const reader = source.getReader();
    let ended = false;
    const end = (outcome: "returned" | "raised" | "cancelled") => {
      if (ended) return;
      ended = true;
      try {
        span.setAttribute("mem0.outcome", outcome);
        if (outcome === "raised")
          span.setStatus({ code: SpanStatusCode.ERROR });
        span.end();
      } catch {
        /* nonfatal */
      }
      try {
        reader.releaseLock();
      } catch {
        /* nonfatal */
      }
    };
    const stream = new ReadableStream<unknown>({
      async pull(controller) {
        try {
          const next = await context.with(active, () => reader.read());
          if (next.done) {
            controller.close();
            end("returned");
          } else {
            recordResult(span, next.value);
            if (field(next.value, "type") === "error") {
              try {
                span.setStatus({ code: SpanStatusCode.ERROR });
              } catch {
                /* nonfatal */
              }
            }
            controller.enqueue(next.value);
          }
        } catch (error) {
          end("raised");
          controller.error(error);
        }
      },
      async cancel(reason: unknown) {
        try {
          await context.with(active, () => reader.cancel(reason));
        } catch (error) {
          end("raised");
          throw error;
        } finally {
          end("cancelled");
        }
      },
    });
    const tracedResult = replaceStream(result, stream);
    streamOwnsSpan = true;
    return tracedResult;
  } catch (error) {
    try {
      span.setStatus({ code: SpanStatusCode.ERROR });
    } catch {
      /* nonfatal */
    }
    throw error;
  } finally {
    if (!streamOwnsSpan) {
      try {
        span.end();
      } catch {
        /* nonfatal */
      }
    }
  }
}
