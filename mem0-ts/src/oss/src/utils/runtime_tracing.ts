import {
  context,
  createContextKey,
  SpanStatusCode,
  trace,
  type Attributes,
  type Context,
  type Span,
  type Tracer,
} from "@opentelemetry/api";
import type { BasicTracerProvider } from "@opentelemetry/sdk-trace-base";
import { z } from "zod";

type ObservationType = "generation" | "embedding" | "rerank" | "span";
type RuntimeMethod = (this: unknown, ...args: unknown[]) => unknown;
interface RuntimeState {
  tracerProvider?: BasicTracerProvider;
  tracer?: Tracer;
  providerPromise?: Promise<BasicTracerProvider | undefined>;
  setupError?: Error;
  lifecycleEpoch: number;
  ownsContextManager: boolean;
  ownsGlobalProvider: boolean;
  inferenceSpanKey: symbol;
  inferenceOwnerKey: symbol;
  patchedObjects: WeakSet<object>;
  patchedMethods: WeakMap<object, Map<string, RuntimeMethod>>;
}
const runtimeKey = Symbol.for("mem0.runtime.tracing.v1");
// Internal process registry: the OSS and standalone bundles must share one owner.
const existingRuntime = Reflect.get(globalThis, runtimeKey) as
  | RuntimeState
  | undefined;
const runtime: RuntimeState = existingRuntime ?? {
  lifecycleEpoch: 0,
  ownsContextManager: false,
  ownsGlobalProvider: false,
  inferenceSpanKey: createContextKey("mem0.runtime.inference"),
  inferenceOwnerKey: createContextKey("mem0.runtime.owner"),
  patchedObjects: new WeakSet<object>(),
  patchedMethods: new WeakMap<object, Map<string, RuntimeMethod>>(),
};
if (!existingRuntime) Reflect.set(globalThis, runtimeKey, runtime);
const { inferenceSpanKey, inferenceOwnerKey, patchedObjects, patchedMethods } =
  runtime;

function field(value: unknown, key: string): unknown {
  if (
    (typeof value !== "object" || value === null) &&
    typeof value !== "function"
  )
    return undefined;
  try {
    return Reflect.get(value, key);
  } catch {
    return undefined;
  }
}

function method(value: unknown): value is RuntimeMethod {
  return typeof value === "function";
}

function safe(action: () => void): void {
  try {
    action();
  } catch {
    /* Observability must not replace inference behavior. */
  }
}

function requestModel(args: unknown[]): unknown {
  return field(args[0], "model") ?? field(args[0], "modelId");
}

function setModel(span: Span, model: unknown): void {
  if (typeof model === "string" && model)
    safe(() =>
      span.setAttribute("langfuse.observation.model.name", model.slice(0, 512)),
    );
}

const usageSchema = z.object({
  prompt_tokens: z.number().nonnegative().optional(),
  input_tokens: z.number().nonnegative().optional(),
  completion_tokens: z.number().nonnegative().optional(),
  output_tokens: z.number().nonnegative().optional(),
  promptTokenCount: z.number().nonnegative().optional(),
  candidatesTokenCount: z.number().nonnegative().optional(),
  inputTokens: z.number().nonnegative().optional(),
  outputTokens: z.number().nonnegative().optional(),
  total_tokens: z.number().nonnegative().optional(),
});
const responseSchema = z.object({
  model: z.string().optional(),
  modelId: z.string().optional(),
  usage: usageSchema.optional(),
  usageMetadata: usageSchema.optional(),
  response: z
    .object({ model: z.string().optional(), usage: usageSchema.optional() })
    .optional(),
  prompt_eval_count: z.number().nonnegative().optional(),
  eval_count: z.number().nonnegative().optional(),
});

export function recordRuntimeResponse(span: Span, result: unknown): void {
  safe(() => {
    const parsed = responseSchema.safeParse(result);
    if (!parsed.success) return;
    const response = parsed.data;
    setModel(
      span,
      response.model ?? response.modelId ?? response.response?.model,
    );
    const usage =
      response.usage ?? response.usageMetadata ?? response.response?.usage;
    const input =
      usage?.prompt_tokens ??
      usage?.input_tokens ??
      usage?.promptTokenCount ??
      usage?.inputTokens ??
      response.prompt_eval_count;
    const output =
      usage?.completion_tokens ??
      usage?.output_tokens ??
      usage?.candidatesTokenCount ??
      usage?.outputTokens ??
      response.eval_count;
    const details: Record<string, number> = {};
    if (input !== undefined && Number.isFinite(input)) details.input = input;
    if (output !== undefined && Number.isFinite(output))
      details.output = output;
    if (
      usage?.total_tokens !== undefined &&
      Number.isFinite(usage.total_tokens)
    )
      details.total = usage.total_tokens;
    if (Object.keys(details).length)
      span.setAttribute(
        "langfuse.observation.usage_details",
        JSON.stringify(details),
      );
  });
}

function observeResult(
  result: unknown,
  span: Span,
  finish?: (failed: boolean) => void,
): unknown {
  if (method(field(result, "then"))) {
    void Promise.resolve(result)
      .then(
        (value) => {
          recordRuntimeResponse(span, value);
          finish?.(false);
        },
        () => {
          finish?.(true);
        },
      )
      .catch(() => undefined);
  } else {
    recordRuntimeResponse(span, result);
    finish?.(false);
  }
  return result;
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

function patch(
  owner: unknown,
  name: string,
  wrap: (original: RuntimeMethod) => RuntimeMethod,
): void {
  if (typeof owner !== "object" || owner === null) return;
  const original = field(owner, name);
  if (!method(original)) return;
  let methods = patchedMethods.get(owner);
  if (!methods) {
    methods = new Map();
    patchedMethods.set(owner, methods);
  }
  if (methods.get(name) === original) return;
  const wrapped = wrap(original);
  if (Reflect.set(owner, name, wrapped)) methods.set(name, wrapped);
}

/** Raw SDK calls enrich an adapter observation without duplicating generations. */
export function instrumentRuntimeClient(
  client: unknown,
  provider: string,
): void {
  safe(() => {
    const chat = field(client, "chat");
    const models = field(client, "models");
    const targets: Array<[unknown, string, ObservationType]> = [
      [field(chat, "completions"), "create", "generation"],
      [field(client, "responses"), "create", "generation"],
      [field(client, "embeddings"), "create", "embedding"],
      [field(client, "messages"), "create", "generation"],
      [chat, "complete", "generation"],
      [models, "generateContent", "generation"],
      [models, "embedContent", "embedding"],
      [client, "chat", "generation"],
      [client, "embed", "embedding"],
      [client, "embedMany", "embedding"],
      [client, "rerank", "rerank"],
      [models, "rerank", "rerank"],
      [client, "predict", "embedding"],
      [client, "invoke", "generation"],
      [client, "generateContent", "generation"],
      [client, "call", "generation"],
    ];
    for (const [owner, name, kind] of targets)
      patch(
        owner,
        name,
        (original) =>
          function (this: unknown, ...args: unknown[]) {
            if (!runtime.tracer || !enabled())
              return original.apply(this, args);
            let parent: Context;
            let active: unknown;
            try {
              parent = context.active();
              active = parent.getValue(inferenceSpanKey);
            } catch {
              return original.apply(this, args);
            }
            if (isSpan(active)) {
              safe(() => setModel(active, requestModel(args)));
              return observeResult(original.apply(this, args), active);
            }
            let span: Span;
            let activeContext: Context;
            try {
              span = runtime.tracer.startSpan(
                `mem0.${kind}.${provider}.${name}`,
                {
                  attributes: attrs(kind, { provider }),
                },
              );
              activeContext = trace
                .setSpan(parent, span)
                .setValue(inferenceSpanKey, span);
            } catch {
              return original.apply(this, args);
            }
            safe(() => setModel(span, requestModel(args)));
            const finish = (failed: boolean) =>
              safe(() => {
                if (failed) span.setStatus({ code: SpanStatusCode.ERROR });
                span.end();
              });
            let invoked = false;
            try {
              const result = context.with(activeContext, () => {
                invoked = true;
                return original.apply(this, args);
              });
              return observeResult(result, span, finish);
            } catch (error) {
              finish(true);
              if (!invoked) return original.apply(this, args);
              throw error;
            }
          },
      );
  });
}

function isSpan(value: unknown): value is Span {
  return (
    typeof value === "object" &&
    value !== null &&
    method(field(value, "spanContext")) &&
    method(field(value, "setAttribute"))
  );
}

/** Cover direct provider imports and factory use, including local inference. */
export function traceRuntimeObject<T extends object>(
  instance: T,
  provider: string,
): T {
  if (patchedObjects.has(instance)) return instance;
  patchedObjects.add(instance);
  const clients = [
    "openai",
    "client",
    "ollama",
    "google",
    "vertexAI",
    "llmInstance",
  ];
  const attach = () => {
    for (const key of clients)
      instrumentRuntimeClient(field(instance, key), provider);
  };
  safe(attach);
  for (const lazy of ["ensureClient", "getClient"])
    safe(() =>
      patch(
        instance,
        lazy,
        (original) =>
          function (this: unknown, ...args: unknown[]) {
            const result = original.apply(this, args);
            if (method(field(result, "then")))
              void Promise.resolve(result).then(
                () => safe(attach),
                () => undefined,
              );
            else safe(attach);
            return result;
          },
      ),
    );
  for (const name of [
    "generateResponse",
    "generateChat",
    "embed",
    "embedBatch",
    "rerank",
    "doGenerate",
    "doStream",
  ])
    safe(() =>
      patch(
        instance,
        name,
        (original) =>
          function (this: unknown, ...args: unknown[]) {
            if (!enabled()) return original.apply(this, args);
            if (context.active().getValue(inferenceOwnerKey) === instance)
              return original.apply(this, args);
            const kind: ObservationType = name.startsWith("embed")
              ? "embedding"
              : name === "rerank"
                ? "rerank"
                : "generation";
            return withRuntimeSpan(
              `mem0.${kind}.${provider}.${name}`,
              kind,
              {
                provider,
                model:
                  field(instance, "model") ??
                  field(instance, "modelId") ??
                  field(instance, "modelName") ??
                  field(field(instance, "config"), "model"),
              },
              () =>
                context.with(
                  context.active().setValue(inferenceOwnerKey, instance),
                  () => {
                    safe(attach);
                    return original.apply(this, args);
                  },
                ),
            );
          },
      ),
    );
  return instance;
}

export interface RuntimeTracingConfig {
  baseUrl?: string;
  publicKey?: string;
  secretKey?: string;
  timeoutMs?: number;
}
const enabled = () =>
  !!runtime.tracer ||
  (typeof process !== "undefined" &&
    process.env?.MEM0_RUNTIME_TRACING === "true");

async function ensureProvider(
  config: RuntimeTracingConfig = {},
  explicit = false,
): Promise<BasicTracerProvider | undefined> {
  if (runtime.tracerProvider) return runtime.tracerProvider;
  if (!enabled() && !explicit) return undefined;
  if (!runtime.providerPromise) {
    const epoch = ++runtime.lifecycleEpoch;
    runtime.providerPromise = Promise.resolve()
      .then(async () => {
        const env = typeof process !== "undefined" ? process.env : {};
        const publicKey = config.publicKey ?? env.LANGFUSE_PUBLIC_KEY;
        const secretKey = config.secretKey ?? env.LANGFUSE_SECRET_KEY;
        const baseUrl =
          config.baseUrl ?? env.LANGFUSE_BASE_URL ?? "http://localhost:4000";
        try {
          if (
            !publicKey ||
            !secretKey ||
            /[\r\n]/.test(publicKey + secretKey) ||
            publicKey.includes(":") ||
            typeof Buffer === "undefined"
          )
            throw new Error();
          const url = new URL(baseUrl);
          const hostname = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
          if (
            !["http:", "https:"].includes(url.protocol) ||
            ![
              "localhost",
              "127.0.0.1",
              "::1",
              "host.docker.internal",
              "langfuse",
              "langfuse-web",
            ].includes(hostname) ||
            url.username ||
            url.password ||
            !["", "/"].includes(url.pathname) ||
            url.search ||
            url.hash
          )
            throw new Error();
          const [
            { BasicTracerProvider, BatchSpanProcessor },
            { OTLPTraceExporter },
            { AsyncLocalStorageContextManager },
          ] = await Promise.all([
            import("@opentelemetry/sdk-trace-base"),
            import("@opentelemetry/exporter-trace-otlp-http"),
            import("@opentelemetry/context-async-hooks"),
          ]);
          if (epoch !== runtime.lifecycleEpoch) return undefined;
          const exporter = new OTLPTraceExporter({
            url: `${url.toString().replace(/\/$/, "")}/api/public/otel/v1/traces`,
            headers: {
              Authorization: `Basic ${Buffer.from(`${publicKey}:${secretKey}`).toString("base64")}`,
              "x-langfuse-ingestion-version": "4",
            },
            timeoutMillis: config.timeoutMs ?? 5000,
          });
          const provider = new BasicTracerProvider({
            spanProcessors: [new BatchSpanProcessor(exporter)],
          });
          const manager = new AsyncLocalStorageContextManager().enable();
          runtime.ownsContextManager = context.setGlobalContextManager(manager);
          if (!runtime.ownsContextManager) manager.disable();
          runtime.ownsGlobalProvider = trace.setGlobalTracerProvider(provider);
          runtime.tracerProvider = provider;
          runtime.tracer = provider.getTracer("mem0.runtime", "1");
          runtime.setupError = undefined;
          return provider;
        } catch {
          runtime.setupError = new Error(
            "Runtime tracing requires optional OTEL packages and valid local Langfuse configuration",
          );
          return undefined;
        }
      })
      .finally(() => {
        if (epoch === runtime.lifecycleEpoch)
          runtime.providerPromise = undefined;
      });
  }
  const provider = await runtime.providerPromise;
  if (explicit && !provider)
    throw runtime.setupError ?? new Error("Runtime tracing setup was canceled");
  return provider;
}

export async function configureRuntimeTracing(
  config: RuntimeTracingConfig = {},
): Promise<void> {
  await ensureProvider(config, true);
}
export async function flushRuntimeTracing(): Promise<void> {
  try {
    await runtime.tracerProvider?.forceFlush();
  } catch {
    /* nonfatal */
  }
}
export async function shutdownRuntimeTracing(): Promise<void> {
  const provider = runtime.tracerProvider,
    ownedContext = runtime.ownsContextManager,
    ownedProvider = runtime.ownsGlobalProvider;
  const epoch = ++runtime.lifecycleEpoch;
  runtime.tracerProvider = undefined;
  runtime.tracer = undefined;
  runtime.providerPromise = undefined;
  runtime.ownsContextManager = false;
  runtime.ownsGlobalProvider = false;
  try {
    await provider?.shutdown();
  } catch {
    /* nonfatal */
  }
  if (epoch === runtime.lifecycleEpoch) {
    if (ownedProvider) trace.disable();
    if (ownedContext) context.disable();
  }
}
export function runtimeTracingEnabled(): boolean {
  return !!runtime.tracer;
}
export function runtimeTracingSetupError(): Error | undefined {
  return runtime.setupError;
}

function attrs(
  kind: ObservationType,
  metadata?: Record<string, unknown>,
): Attributes {
  const result: Attributes = {
    "langfuse.observation.type": kind === "rerank" ? "span" : kind,
    "mem0.attempt_scope": "logical_provider_invocation",
  };
  for (const key of [
    "provider",
    "model",
    "requestId",
    "caseId",
    "runId",
    "mode",
    "synthetic",
  ]) {
    const value = metadata?.[key];
    if (typeof value === "string")
      result[`langfuse.observation.metadata.${key}`] = value.slice(0, 256);
    else if (typeof value === "boolean")
      result[`langfuse.observation.metadata.${key}`] = value;
  }
  return result;
}

export async function withRuntimeSpan<T>(
  name: string,
  kind: ObservationType,
  metadata: Record<string, unknown> | undefined,
  fn: (span?: Span) => T | Promise<T>,
): Promise<T> {
  if (!enabled()) return fn();
  if (!runtime.tracer) await ensureProvider();
  if (!runtime.tracer) return fn();
  let span: Span;
  let activeContext: Context;
  try {
    span = runtime.tracer.startSpan(name, {
      attributes: attrs(kind, metadata),
    });
    setModel(span, metadata?.model);
    activeContext = trace.setSpan(context.active(), span);
    if (kind !== "span")
      activeContext = activeContext.setValue(inferenceSpanKey, span);
  } catch {
    return fn();
  }
  let started = false;
  let streamOwnsSpan = false;
  try {
    return await context.with(activeContext, async () => {
      started = true;
      try {
        const result = await fn(span);
        recordRuntimeResponse(span, result);
        const source = field(result, "stream");
        if (
          typeof ReadableStream !== "undefined" &&
          source instanceof ReadableStream &&
          !source.locked &&
          typeof result === "object" &&
          result !== null
        ) {
          const reader = source.getReader();
          let ended = false;
          const end = (outcome: "returned" | "raised" | "cancelled") => {
            if (ended) return;
            ended = true;
            safe(() => {
              span.setAttribute("mem0.outcome", outcome);
              if (outcome === "raised")
                span.setStatus({ code: SpanStatusCode.ERROR });
              span.end();
            });
            safe(() => reader.releaseLock());
          };
          const stream = new ReadableStream<unknown>({
            async pull(controller) {
              try {
                const next = await context.with(activeContext, () =>
                  reader.read(),
                );
                if (next.done) {
                  controller.close();
                  end("returned");
                } else {
                  recordRuntimeResponse(span, next.value);
                  if (field(next.value, "type") === "error")
                    safe(() => span.setStatus({ code: SpanStatusCode.ERROR }));
                  controller.enqueue(next.value);
                }
              } catch (error) {
                end("raised");
                controller.error(error);
              }
            },
            async cancel(reason: unknown) {
              try {
                await context.with(activeContext, () => reader.cancel(reason));
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
        }
        return result;
      } catch (error) {
        safe(() => span.setStatus({ code: SpanStatusCode.ERROR }));
        throw error;
      } finally {
        if (!streamOwnsSpan) safe(() => span.end());
      }
    });
  } catch (error) {
    if (!started) {
      safe(() => span.end());
      return fn();
    }
    throw error;
  }
}
