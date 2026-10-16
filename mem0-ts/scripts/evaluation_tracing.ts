/** Evaluation-local OTel lifecycle: never registers a global provider or context manager. */
import {
  ROOT_CONTEXT,
  trace,
  SpanStatusCode,
  type Span,
  type Attributes,
} from "@opentelemetry/api";
import { AsyncLocalStorageContextManager } from "@opentelemetry/context-async-hooks";
import {
  BasicTracerProvider,
  BatchSpanProcessor,
  type SpanExporter,
} from "@opentelemetry/sdk-trace-base";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { createHash } from "node:crypto";
export interface TracingConfig {
  baseUrl: string;
  publicKey: string;
  secretKey: string;
  timeoutMs?: number;
  maxQueueSize?: number;
  recordContent?: boolean;
}
export function tracingConfigFromEnv(): TracingConfig {
  const {
    LANGFUSE_BASE_URL: baseUrl,
    LANGFUSE_PUBLIC_KEY: publicKey,
    LANGFUSE_SECRET_KEY: secretKey,
  } = process.env;
  if (!baseUrl || !publicKey || !secretKey)
    throw new Error("Langfuse requires explicit base URL and project keys");
  validateUrl(baseUrl);
  return { baseUrl, publicKey, secretKey };
}
function validateUrl(base: string): URL {
  const url = new URL(base);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new Error("Invalid Langfuse base URL");
  return url;
}
export interface TraceReport {
  created: number;
  ended: number;
  exported: number;
  failed: number;
  unknown: number;
  operationErrors: number;
  timedOut: boolean;
}
export interface SpanHandle {
  span: Span | undefined;
  run<T>(fn: () => Promise<T>): Promise<T>;
  set(attributes: Attributes): void;
  end(status?: string): void;
}
const noSpan: SpanHandle = {
  span: undefined,
  run: async (fn) => fn(),
  set: () => {},
  end: () => {},
};
const digest = (v: unknown) =>
  createHash("sha256")
    .update(JSON.stringify(v) ?? "undefined")
    .digest("hex");
export class EvaluationTracing {
  private manager = new AsyncLocalStorageContextManager().enable();
  private provider: BasicTracerProvider;
  private metadata = new Map<string, Attributes>();
  readonly report: TraceReport = {
    created: 0,
    ended: 0,
    exported: 0,
    failed: 0,
    unknown: 0,
    operationErrors: 0,
    timedOut: false,
  };
  constructor(
    readonly config: TracingConfig,
    exporter?: SpanExporter,
  ) {
    if (
      !Number.isInteger(config.timeoutMs ?? 5000) ||
      (config.timeoutMs ?? 5000) < 1 ||
      (config.timeoutMs ?? 5000) > 30000 ||
      !Number.isInteger(config.maxQueueSize ?? 2048) ||
      (config.maxQueueSize ?? 2048) < 1 ||
      (config.maxQueueSize ?? 2048) > 100000
    )
      throw new Error("Invalid tracing limits");
    const url = validateUrl(config.baseUrl);
    const timeoutMillis = config.timeoutMs ?? 5000;
    const transport =
      exporter ??
      new OTLPTraceExporter({
        url: `${url.toString().replace(/\/$/, "")}/api/public/otel/v1/traces`,
        headers: {
          Authorization: `Basic ${Buffer.from(`${config.publicKey}:${config.secretKey}`).toString("base64")}`,
          "x-langfuse-ingestion-version": "4",
        },
        timeoutMillis,
      });
    const counted: SpanExporter = {
      export: (spans, callback) => {
        let settled = false;
        const finish: Parameters<SpanExporter["export"]>[1] = (result) => {
          if (settled) return;
          settled = true;
          if (result.code === 0) this.report.exported += spans.length;
          else this.report.failed += spans.length;
          callback({ code: result.code }); // Never expose transport errors or response bodies.
        };
        try {
          transport.export(spans, finish);
        } catch {
          finish({ code: 1 });
        }
      },
      shutdown: async () => {
        await transport.shutdown();
      },
    };
    this.provider = new BasicTracerProvider({
      spanProcessors: [
        new BatchSpanProcessor(counted, {
          maxQueueSize: config.maxQueueSize ?? 2048,
          maxExportBatchSize: Math.min(config.maxQueueSize ?? 2048, 512),
          scheduledDelayMillis: 60000,
          exportTimeoutMillis: timeoutMillis,
        }),
      ],
    });
  }
  activeSpan(): Span | undefined {
    return trace.getSpan(this.manager.active());
  }
  start(
    name: string,
    type = "span",
    metadata?: Record<string, unknown>,
    root = false,
  ): SpanHandle {
    try {
      const parent = root ? ROOT_CONTEXT : this.manager.active();
      const inherited =
        this.metadata.get(trace.getSpan(parent)?.spanContext().traceId ?? "") ??
        {};
      const attributes: Attributes = {
        ...inherited,
        "langfuse.observation.type": type,
      };
      for (const [key, value] of Object.entries(metadata ?? {}).slice(0, 48)) {
        if (
          /secret|password|credential|authorization|cookie|api.?key/i.test(key)
        )
          continue;
        if (
          typeof value === "string" ||
          typeof value === "boolean" ||
          (typeof value === "number" && Number.isFinite(value))
        ) {
          const bounded =
            typeof value === "string" ? value.slice(0, 512) : value;
          attributes[`langfuse.observation.metadata.${key}`] = bounded;
          attributes[`langfuse.trace.metadata.${key}`] = bounded;
        }
      }
      const span = this.provider
        .getTracer("mem0.evaluation", "1")
        .startSpan(name, { attributes }, parent);
      this.report.created++;
      if (root) this.metadata.set(span.spanContext().traceId, attributes);
      const active = trace.setSpan(parent, span);
      let ended = false;
      return {
        span,
        run: (fn) => this.manager.with(active, fn),
        set: (values) => {
          try {
            span.setAttributes(values);
          } catch {
            this.report.operationErrors++;
          }
        },
        end: (status) => {
          if (ended) return;
          ended = true;
          try {
            if (status)
              span.setAttributes({
                "langfuse.observation.metadata.status": status,
                "langfuse.trace.metadata.status": status,
              });
            if (status === "error")
              span.setStatus({ code: SpanStatusCode.ERROR });
            span.end();
            this.report.ended++;
          } catch {
            this.report.operationErrors++;
          }
          if (root) this.metadata.delete(span.spanContext().traceId);
        },
      };
    } catch {
      this.report.operationErrors++;
      return noSpan;
    }
  }
  content(handle: SpanHandle, input: unknown, output?: unknown): void {
    try {
      handle.set({
        "langfuse.observation.metadata.input_sha256": digest(input),
        ...(output === undefined
          ? {}
          : { "langfuse.observation.metadata.output_sha256": digest(output) }),
      });
      if (this.config.recordContent) {
        // Embeddings remain hashes; never export vectors. Bound approved content.
        const clean = (v: unknown): unknown =>
          Array.isArray(v)
            ? v.slice(0, 64).map(clean)
            : v && typeof v === "object"
              ? Object.fromEntries(
                  Object.entries(v)
                    .slice(0, 64)
                    .map(([k, x]) => [
                      k,
                      /secret|password|credential|authorization|cookie|api.?key|embedding/i.test(
                        k,
                      )
                        ? "[redacted]"
                        : clean(x),
                    ]),
                )
              : typeof v === "string"
                ? v.slice(0, 4096)
                : v;
        handle.set({
          "langfuse.observation.input": JSON.stringify(clean(input)).slice(
            0,
            8192,
          ),
          ...(output === undefined
            ? {}
            : {
                "langfuse.observation.output": JSON.stringify(
                  clean(output),
                ).slice(0, 8192),
              }),
        });
      }
    } catch {
      this.report.operationErrors++;
    }
  }
  response(handle: SpanHandle, result: any): void {
    try {
      if (typeof result?.model === "string")
        handle.set({
          "langfuse.observation.model.name": result.model.slice(0, 512),
          "gen_ai.response.model": result.model.slice(0, 512),
        });
      for (const field of [
        "total_duration",
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
      ]) {
        if (
          typeof result?.[field] === "number" &&
          Number.isFinite(result[field]) &&
          result[field] >= 0
        )
          handle.set({
            [`langfuse.observation.metadata.${field}_ns`]: result[field],
          });
      }
      const usage = result?.usage;
      const input =
        usage?.prompt_tokens ??
        usage?.input_tokens ??
        result?.prompt_eval_count;
      const output =
        usage?.completion_tokens ?? usage?.output_tokens ?? result?.eval_count;
      const details: Record<string, number> = {};
      if (typeof input === "number" && Number.isFinite(input) && input >= 0)
        details.input = input;
      if (typeof output === "number" && Number.isFinite(output) && output >= 0)
        details.output = output;
      if (
        typeof usage?.total_tokens === "number" &&
        Number.isFinite(usage.total_tokens) &&
        usage.total_tokens >= 0
      )
        details.total = usage.total_tokens;
      if (Object.keys(details).length)
        handle.set({
          "langfuse.observation.usage_details": JSON.stringify(details),
          ...(details.input === undefined
            ? {}
            : { "gen_ai.usage.input_tokens": details.input }),
          ...(details.output === undefined
            ? {}
            : { "gen_ai.usage.output_tokens": details.output }),
        });
    } catch {
      this.report.operationErrors++;
    }
  }
  async shutdown(): Promise<TraceReport> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        (async () => {
          try {
            await this.provider.forceFlush();
          } catch {
            this.report.operationErrors++;
            if (this.report.ended > this.report.exported + this.report.failed)
              this.report.timedOut = true;
          }
          await this.provider.shutdown();
        })(),
        new Promise<void>((resolve) => {
          timer = setTimeout(() => {
            this.report.timedOut = true;
            resolve();
          }, this.config.timeoutMs ?? 5000);
        }),
      ]);
    } catch {
      this.report.operationErrors++;
    } finally {
      if (timer) clearTimeout(timer);
      this.manager.disable();
    }
    this.report.unknown = Math.max(
      0,
      this.report.ended - this.report.exported - this.report.failed,
    );
    return { ...this.report };
  }
}
