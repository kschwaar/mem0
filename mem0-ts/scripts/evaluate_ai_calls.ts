/** Focused provider replay; synthetic dry runs never measure model quality. */
import { AsyncLocalStorage } from "node:async_hooks";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, open, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { performance } from "node:perf_hooks";
import {
  ADDITIVE_EXTRACTION_PROMPT,
  AGENT_CONTEXT_SUFFIX,
  AdditiveExtractionSchema,
  generateAdditiveExtractionPrompt,
} from "../src/oss/src/prompts";
import type { LLM } from "../src/oss/src/llms/base";
import type { Embedder } from "../src/oss/src/embeddings/base";
import type { Reranker } from "../src/oss/src/rerankers/base";

import {
  EvaluationTracing,
  tracingConfigFromEnv,
  type TracingConfig,
} from "./evaluation_tracing";
import { createScoreOutbox, type ScoreOutbox } from "./evaluation_scores";

type Json = Record<string, any>;
export interface EvalCase {
  id: string;
  task: "llm" | "embedding" | "rerank";
  input: Json;
  expect: Json;
  mock_output?: any;
  mock_error?: string;
  provenance?: Json;
  typescript?: {
    skip?: string;
    expect?: Json;
    mock_output?: any;
    provenance?: Json;
  };
}
export interface ProviderSpec {
  provider: string;
  config: Json;
}
export interface Options {
  traceBackend?: "none" | "langfuse";
  traceConfig?: TracingConfig;
  tracing?: EvaluationTracing;
  offline: boolean;
  repeats: number;
  concurrency: number;
  out: string;
  config: Partial<Record<"llm" | "embedder" | "reranker", ProviderSpec>>;
  maxP95Ms?: number;
  minPassRate?: number;
  recordContent?: boolean;
}
export interface Providers {
  llm?: LLM;
  embedder?: Embedder;
  reranker?: Reranker;
}
interface SdkSpan {
  span_id?: string;
  parent_call_id?: string;
  run_id?: string;
  case_id?: string;
  started_at?: string;
  durations_ns?: Json;
  response_model?: string | null;
  method: string;
  latency_ms: number;
  success: boolean;
  error_type?: string;
  usage: Json | null;
}
interface TraceContext {
  tracing?: EvaluationTracing;
  callId: string;
  runId: string;
  caseId: string;
  task: string;
  sdk: SdkSpan[];
  fallback: boolean;
}
export interface RecordResult {
  trace_id?: string;
  root_observation_id?: string;
  run_id?: string;
  call_id?: string;
  started_at?: string;
  case_id: string;
  task: string;
  repeat: number;
  mode: "offline_synthetic" | "live";
  status: "pass" | "fail" | "error" | "skip";
  reason?: string;
  latency_ms: number | null;
  request_sha256: string | null;
  response_sha256?: string;
  semantic_failures?: string[];
  fallback_detected?: boolean | null;
  sdk_spans?: SdkSpan[];
  usage: null;
  provider_attempts: null;
  provenance?: Json;
  content?: Json;
}
const context = new AsyncLocalStorage<TraceContext>();
const patched = new WeakSet<object>();
function canonical(value: any): any {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((k) => [k, canonical(value[k])]),
    );
  return value;
}
export const hash = (value: unknown): string =>
  createHash("sha256")
    .update(JSON.stringify(canonical(value)) ?? "undefined")
    .digest("hex");
export function configMetadata(config: Options["config"]): Json {
  const allowed = new Set([
    "model",
    "temperature",
    "maxTokens",
    "topP",
    "topK",
    "embeddingDims",
    "timeout",
    "isReasoningModel",
    "returnDocuments",
    "maxChunksPerDoc",
  ]);
  function spec(value: ProviderSpec): Json {
    return {
      provider: value.provider,
      config: Object.fromEntries(
        Object.entries(value.config).filter(
          ([key, item]) =>
            allowed.has(key) &&
            ["string", "number", "boolean"].includes(typeof item),
        ),
      ),
      ...(value.config.llm ? { llm: spec(value.config.llm) } : {}),
    };
  }
  return Object.fromEntries(
    Object.entries(config).map(([key, value]) => [key, spec(value!)]),
  );
}
async function sourceMetadata(): Promise<Json> {
  const run = promisify(execFile);
  let gitSha: string | null = null;
  let dirty: boolean | null = null;
  try {
    const cwd = resolve(__dirname, "../..");
    gitSha = (await run("git", ["rev-parse", "HEAD"], { cwd })).stdout.trim();
    dirty = !!(
      await run("git", ["status", "--porcelain"], { cwd })
    ).stdout.trim();
  } catch {
    /* Portable snapshots may not contain git metadata. */
  }
  return {
    git_sha: gitSha,
    git_dirty: dirty,
    script_sha256: createHash("sha256")
      .update(await readFile(__filename))
      .digest("hex"),
    ts_prompt_source_sha256: createHash("sha256")
      .update(
        await readFile(resolve(__dirname, "../src/oss/src/prompts/index.ts")),
      )
      .digest("hex"),
  };
}
export function validateExpectations(c: EvalCase): void {
  const allowed = new Set(
    c.task === "llm"
      ? [
          "json_required",
          "json_type",
          "required_keys",
          "equals",
          "contains",
          "additive_schema",
          "memory_count",
          "memory_items",
          "tool_names",
        ]
      : c.task === "embedding"
        ? ["finite_vectors", "dimensions"]
        : ["top_id"],
  );
  for (const [key, value] of Object.entries(c.expect)) {
    if (!allowed.has(key)) throw new Error(`Unsupported expectation: ${key}`);
    if (
      ["json_required", "additive_schema", "finite_vectors"].includes(key) &&
      value !== true
    )
      throw new Error(`Vacuous expectation: ${key}`);
    if (key === "json_type" && !["object", "array"].includes(value))
      throw new Error("Unsupported json_type");
    if (
      ["required_keys", "contains", "memory_items"].includes(key) &&
      (!Array.isArray(value) || !value.length)
    )
      throw new Error(`Vacuous expectation: ${key}`);
    if (
      ["required_keys", "contains", "tool_names"].includes(key) &&
      (!Array.isArray(value) || value.some((v) => typeof v !== "string" || !v))
    )
      throw new Error(`Invalid expectation: ${key}`);
    if (key === "memory_count" && (!Number.isInteger(value) || value < 0))
      throw new Error("Invalid memory_count");
    if (key === "dimensions" && (!Number.isInteger(value) || value < 1))
      throw new Error("Invalid dimensions");
    if (key === "top_id" && (typeof value !== "string" || !value))
      throw new Error("Invalid top_id");
    if (key === "memory_items")
      for (const item of value) {
        if (!item || typeof item !== "object" || !Object.keys(item).length)
          throw new Error("Empty memory expectation");
        for (const [field, expected] of Object.entries(item)) {
          if (!["id", "attributed_to", "contains"].includes(field))
            throw new Error(
              `Unsupported TypeScript memory expectation: ${field}`,
            );
          if (
            field === "contains"
              ? !Array.isArray(expected) ||
                !expected.length ||
                expected.some((v) => typeof v !== "string" || !v)
              : typeof expected !== "string" || !expected
          )
            throw new Error("Invalid memory expectation");
        }
      }
  }
}
/** Do not persist keys, URL credentials, arbitrary objects, or error messages. */
export function redact(value: any, key = ""): any {
  if (
    /key|secret|token|password|credential|authorization|header|cookie/i.test(
      key,
    )
  )
    return "[redacted]";
  if (Array.isArray(value)) return value.map((x) => redact(x));
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, redact(v, k)]),
    );
  if (typeof value === "string" && /url|host|endpoint/i.test(key)) {
    try {
      const url = new URL(value);
      url.username = "";
      url.password = "";
      url.search = "";
      url.hash = "";
      return url.toString();
    } catch {
      return "[invalid URL]";
    }
  }
  return value;
}
export function prepareCase(original: EvalCase): EvalCase {
  const c = structuredClone(original);
  if (c.typescript) {
    c.expect = c.typescript.expect ?? c.expect;
    if (Object.prototype.hasOwnProperty.call(c.typescript, "mock_output"))
      c.mock_output = c.typescript.mock_output;
    c.provenance = { ...c.provenance, ...c.typescript.provenance };
  }
  return c;
}
export function skipReason(c: EvalCase): string | undefined {
  if (c.typescript?.skip) return c.typescript.skip;
  if (c.input.stage === "relationship_extraction")
    return "No equivalent TypeScript production relationship LLM stage; Python fixture skipped.";
  if (c.input.tool_choice && c.input.tool_choice !== "auto")
    return "TypeScript LLM adapter interface does not support explicit tool_choice.";
  return undefined;
}
export function renderInput(c: EvalCase): Json {
  const input = { ...c.input };
  if (input.stage === "additive_extraction") {
    const args = input.prompt_args ?? {};
    const supported = new Set([
      "existing_memories",
      "new_messages",
      "last_k_messages",
      "custom_instructions",
      "current_date",
      "timestamp",
    ]);
    for (const key of Object.keys(args))
      if (!supported.has(key))
        throw new Error(`Unsupported TypeScript prompt argument: ${key}`);
    input.messages = [
      {
        role: "system",
        content:
          ADDITIVE_EXTRACTION_PROMPT +
          (input.agent_scoped ? AGENT_CONTEXT_SUFFIX : ""),
      },
      {
        role: "user",
        content: generateAdditiveExtractionPrompt({
          existingMemories: args.existing_memories,
          newMessages:
            typeof args.new_messages === "string"
              ? args.new_messages
              : JSON.stringify(args.new_messages ?? []),
          lastKMessages: args.last_k_messages,
          customInstructions: args.custom_instructions,
          currentDate: args.current_date,
          observationDate: args.timestamp,
        }),
      },
    ];
  } else if (input.stage !== undefined) {
    throw new Error(`Unsupported stage: ${input.stage}`);
  }
  delete input.stage;
  delete input.prompt_args;
  delete input.agent_scoped;
  delete input.tool_choice;
  return input;
}
function parsedOutput(output: any): any {
  if (typeof output !== "string") return output;
  return JSON.parse(output);
}
export function grade(
  c: EvalCase,
  output: any,
  configuredDims?: number,
): string[] {
  const fail: string[] = [];
  const expected = c.expect;
  let parsed: any = output;
  const needJson =
    c.task === "llm" &&
    (expected.json_required ||
      expected.json_type ||
      expected.required_keys ||
      expected.memory_items ||
      expected.memory_count !== undefined ||
      expected.additive_schema ||
      "equals" in expected);
  if (needJson) {
    try {
      parsed = parsedOutput(
        typeof output === "object" && output?.content ? output.content : output,
      );
    } catch {
      return ["invalid_json"];
    }
  }
  if (
    expected.json_type === "object" &&
    (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
  )
    fail.push("json_type");
  if (expected.json_type === "array" && !Array.isArray(parsed))
    fail.push("json_type");
  for (const key of expected.required_keys ?? [])
    if (!parsed || !Object.prototype.hasOwnProperty.call(parsed, key))
      fail.push(`missing_key:${key}`);
  if ("equals" in expected && hash(parsed) !== hash(expected.equals))
    fail.push("equals");
  const text = typeof output === "string" ? output : JSON.stringify(output);
  for (const part of expected.contains ?? [])
    if (!text.includes(part)) fail.push(`contains:${part}`);
  if (expected.additive_schema) {
    const result = AdditiveExtractionSchema.safeParse(parsed);
    if (!result.success) fail.push("additive_schema");
    else {
      const ids = result.data.memory.map((m) => m.id);
      if (new Set(ids).size !== ids.length) fail.push("duplicate_memory_id");
      for (const m of result.data.memory) {
        if (!m.text.trim()) fail.push("empty_memory_text");
        if (!m.attributed_to) fail.push("missing_attribution");
      }
    }
  }
  if (
    expected.memory_count !== undefined &&
    (!Array.isArray(parsed?.memory) ||
      parsed.memory.length !== expected.memory_count)
  )
    fail.push("memory_count");
  for (const item of expected.memory_items ?? []) {
    const matched =
      Array.isArray(parsed?.memory) &&
      parsed.memory.some((m: Json) =>
        Object.entries(item).every(([key, value]) =>
          key === "contains"
            ? (value as string[]).every(
                (word) =>
                  typeof m.text === "string" &&
                  m.text.toLowerCase().includes(word.toLowerCase()),
              )
            : m[key] === value,
        ),
      );
    if (!matched) fail.push(`memory_item:${JSON.stringify(item)}`);
  }
  if (expected.tool_names !== undefined) {
    const names = (output?.toolCalls ?? []).map((t: Json) => t.name).sort();
    if (hash(names) !== hash([...expected.tool_names].sort()))
      fail.push("tool_names");
  }
  if (c.task === "embedding") {
    const batch = Array.isArray(c.input.texts);
    const vectors = batch ? output : [output];
    if (
      !Array.isArray(vectors) ||
      vectors.length !== (batch ? c.input.texts.length : 1)
    )
      fail.push("vector_count");
    else {
      const dims = expected.dimensions ?? configuredDims;
      for (const vector of vectors) {
        if (
          !Array.isArray(vector) ||
          vector.length === 0 ||
          vector.some((v) => typeof v !== "number" || !Number.isFinite(v))
        )
          fail.push("finite_vector");
        else if (dims !== undefined && vector.length !== dims)
          fail.push("vector_dimensions");
      }
      if (new Set(vectors.map((v: any) => v?.length)).size > 1)
        fail.push("inconsistent_dimensions");
    }
  }
  if (c.task === "rerank") {
    if (
      !Array.isArray(output) ||
      output.some(
        (d: Json) =>
          typeof d.rerank_score !== "number" ||
          !Number.isFinite(d.rerank_score),
      )
    )
      fail.push("rerank_shape");
    if (expected.top_id !== undefined && output?.[0]?.id !== expected.top_id)
      fail.push("top_id");
  }
  return fail;
}

/** Trace SDK-method completions, not HTTP retries hidden inside an SDK. */
export function instrumentProvider(target: any): void {
  if (!target || typeof target !== "object" || patched.has(target)) return;
  patched.add(target);
  for (const lazy of ["ensureClient", "getClient"]) {
    if (typeof target[lazy] === "function") {
      const original = target[lazy];
      target[lazy] = async function (...args: any[]) {
        const result = await original.apply(this, args);
        instrumentClients(this);
        if (result && typeof result === "object") instrumentSdk(result);
        return result;
      };
    }
  }
  instrumentClients(target);
  if (target.llm) {
    instrumentProvider(target.llm);
    const original = target.llm.generateResponse;
    target.llm.generateResponse = async function (...args: any[]) {
      try {
        const result = await original.apply(this, args);
        const text =
          typeof result === "string" ? result : (result?.content ?? "");
        if (!/-?\d+(?:\.\d+)?/.test(text)) {
          const current = context.getStore();
          if (current) current.fallback = true;
        }
        return result;
      } catch (error) {
        const current = context.getStore();
        if (current) current.fallback = true;
        throw error;
      }
    };
  }
}
function numericUsage(value: any): Json {
  const usage: Json = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === "number" && Number.isFinite(item)) usage[key] = item;
    else if (item && typeof item === "object" && !Array.isArray(item))
      usage[key] = numericUsage(item);
  }
  return usage;
}
function instrumentClients(target: any): void {
  for (const name of ["openai", "client", "ollama", "clientInstance"])
    if (target[name]) instrumentSdk(target[name]);
}
const sdkPatched = new WeakSet<object>();
function instrumentSdk(client: any): void {
  if (!client || sdkPatched.has(client)) return;
  sdkPatched.add(client);
  const methods: [any, string, string][] = [
    [client.chat?.completions, "create", "chat.completions.create"],
    [client.embeddings, "create", "embeddings.create"],
    [client, "chat", "ollama.chat"],
    [client, "embed", "ollama.embed"],
    [client, "rerank", "rerank"],
    [client.models, "rerank", "models.rerank"],
  ];
  for (const [owner, key, method] of methods) {
    if (!owner || typeof owner[key] !== "function") continue;
    const original = owner[key];
    owner[key] = async function (...args: any[]) {
      const current = context.getStore();
      const observed = current?.tracing?.start(
        method,
        /embed/.test(method)
          ? "embedding"
          : /chat/.test(method)
            ? "generation"
            : "span",
      );
      if (typeof args[0]?.model === "string")
        observed?.set({ "gen_ai.request.model": args[0].model.slice(0, 512) });
      if (observed && !/embed/.test(method))
        current?.tracing?.content(observed, args[0]);
      const started = performance.now();
      const spanMeta = {
        span_id:
          observed?.span?.spanContext().spanId ??
          (current?.tracing ? undefined : randomUUID()),
        parent_call_id: current?.callId,
        run_id: current?.runId,
        case_id: current?.caseId,
        started_at: new Date().toISOString(),
      };
      try {
        const invokeSdk = () => original.apply(this, args);
        const result = observed
          ? await observed.run(invokeSdk)
          : await invokeSdk();
        if (observed) {
          current?.tracing?.response(observed, result);
          if (!/embed/.test(method))
            current?.tracing?.content(observed, args[0], result);
        }
        current?.sdk.push({
          ...spanMeta,
          response_model:
            typeof result.model === "string" ? result.model : null,
          durations_ns: Object.fromEntries(
            [
              "total_duration",
              "load_duration",
              "prompt_eval_duration",
              "eval_duration",
            ]
              .filter((k) => typeof result[k] === "number")
              .map((k) => [k, result[k]]),
          ),
          method,
          latency_ms: performance.now() - started,
          success: true,
          usage: result.usage
            ? numericUsage(result.usage)
            : result.prompt_eval_count !== undefined
              ? {
                  input_tokens: result.prompt_eval_count,
                  output_tokens: result.eval_count ?? null,
                }
              : null,
        });
        observed?.end("pass");
        return result;
      } catch (error) {
        observed?.end("error");
        current?.sdk.push({
          ...spanMeta,
          method,
          latency_ms: performance.now() - started,
          success: false,
          error_type: error instanceof Error ? error.name : "unknown",
          usage: null,
        });
        if (current && current.task === "rerank") current.fallback = true;
        throw error;
      }
    };
  }
}
export function resolveEnv(value: any): any {
  if (Array.isArray(value)) return value.map(resolveEnv);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([key, v]) => [key, resolveEnv(v)]),
    );
  if (typeof value === "string") {
    return value.replace(/\$\{([A-Za-z_][A-Za-z_0-9]*)\}/g, (_, name) => {
      const resolved = process.env[name];
      if (!resolved) throw new Error(`Missing environment variable: ${name}`);
      return resolved;
    });
  }
  return value;
}
export async function preflightOllama(spec: ProviderSpec): Promise<void> {
  if (spec.provider !== "ollama") return;
  const base = new URL(
    spec.config.url ?? spec.config.baseURL ?? "http://localhost:11434",
  );
  if (base.username || base.password || base.search)
    throw new Error(
      "Ollama endpoint must not contain credentials or query parameters",
    );
  if (!spec.config.model || /replace|your[_ -]|<|>/i.test(spec.config.model))
    throw new Error("Set an explicit installed Ollama model");
  const response = await fetch(new URL("/api/tags", base), {
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok)
    throw new Error(`Ollama model preflight status ${response.status}`);
  const data = (await response.json()) as { models?: Array<{ name: string }> };
  // LLM constructor checks exact names; require exact match to prevent automatic pulls.
  if (!data.models?.some((m) => m.name === spec.config.model))
    throw new Error(
      "Configured Ollama model is not installed under its exact name; install explicitly first",
    );
}
export async function createProviders(
  config: Options["config"],
  tasks: Set<string>,
): Promise<Providers> {
  // Import factories only for live execution. Offline never instantiates clients.
  const factories = await import("../src/oss/src/utils/factory");
  const providers: Providers = {};
  for (const [task, key] of [
    ["llm", "llm"],
    ["embedding", "embedder"],
    ["rerank", "reranker"],
  ] as const) {
    if (!tasks.has(task)) continue;
    const spec = config[key];
    if (!spec?.provider || !spec.config)
      throw new Error(`Missing provider configuration: ${key}`);
    if (
      ["llm", "embedder"].includes(key) &&
      (!spec.config.model || /replace|your[_ -]|<|>/i.test(spec.config.model))
    )
      throw new Error(
        `Set explicit ${key} model; placeholder/default models are not evaluated`,
      );
    if (
      key === "reranker" &&
      spec.provider === "llm_reranker" &&
      (!spec.config.llm?.provider ||
        !spec.config.llm?.config?.model ||
        /replace|your[_ -]|<|>/i.test(spec.config.llm.config.model))
    )
      throw new Error(
        "LLM reranker needs an explicit nested provider and model",
      );
    await preflightOllama(spec);
    if (key === "reranker" && spec.config.llm?.provider === "ollama")
      await preflightOllama(spec.config.llm);
    const provider =
      key === "llm"
        ? factories.LLMFactory.create(spec.provider, spec.config)
        : key === "embedder"
          ? factories.EmbedderFactory.create(spec.provider, spec.config)
          : factories.RerankerFactory.create(spec.provider, spec.config);
    (providers as Json)[key] = provider;
    instrumentProvider(provider);
  }
  return providers;
}
async function invoke(
  c: EvalCase,
  input: Json,
  providers: Providers,
): Promise<any> {
  if (c.task === "llm")
    return providers.llm!.generateResponse(
      input.messages,
      input.response_format,
      input.tools,
    );
  if (c.task === "embedding")
    return input.texts
      ? providers.embedder!.embedBatch(input.texts, input.memory_action)
      : providers.embedder!.embed(input.text, input.memory_action);
  const documents = input.documents.map((d: any) =>
    typeof d === "string" ? d : (d.memory ?? d.text),
  );
  const result = await providers.reranker!.rerank(
    input.query,
    documents,
    input.top_k,
  );
  return result.map((r) => {
    if (
      !Number.isInteger(r.index) ||
      r.index < 0 ||
      r.index >= documents.length
    )
      throw new Error("Reranker returned invalid document index");
    const original = input.documents[r.index];
    return {
      id: typeof original === "string" ? String(r.index) : original.id,
      memory: documents[r.index],
      rerank_score: r.rerankScore,
    };
  });
}
export function summarize(records: RecordResult[]): Json {
  const measured = records.filter((r) => r.status !== "skip");
  const latencies = measured.map((r) => r.latency_ms!).sort((a, b) => a - b);
  const percentile = (p: number) =>
    latencies.length
      ? latencies[Math.max(0, Math.ceil(p * latencies.length) - 1)]
      : null;
  const passed = measured.filter((r) => r.status === "pass").length;
  const n = measured.length;
  const z = 1.96;
  const rate = n ? passed / n : 0;
  const center = n ? (rate + (z * z) / (2 * n)) / (1 + (z * z) / n) : 0;
  const margin = n
    ? (z * Math.sqrt((rate * (1 - rate)) / n + (z * z) / (4 * n * n))) /
      (1 + (z * z) / n)
    : 0;
  return {
    pass_rate_wilson_95: n
      ? {
          low: Math.max(0, center - margin),
          high: Math.min(1, center + margin),
        }
      : null,
    fallback_unknown_count: measured.filter((r) => r.fallback_detected == null)
      .length,
    total: records.length,
    measured: measured.length,
    passed,
    failed: measured.filter((r) => r.status === "fail").length,
    errors: measured.filter((r) => r.status === "error").length,
    skipped: records.length - measured.length,
    pass_rate: measured.length ? passed / measured.length : null,
    latency_ms: {
      p50: percentile(0.5),
      p95: percentile(0.95),
      p99: percentile(0.99),
    },
    fallback_count: measured.filter((r) => r.fallback_detected).length,
    observed_sdk_calls: measured.reduce(
      (n, r) => n + (r.sdk_spans?.length ?? 0),
      0,
    ),
    usage: null,
    provider_attempts: null,
  };
}
export async function runEvaluation(
  originals: EvalCase[],
  options: Options,
  provided?: Providers,
): Promise<Json> {
  if (
    !Number.isInteger(options.repeats) ||
    options.repeats < 1 ||
    options.repeats > 1000
  )
    throw new Error("repeats must be an integer between 1 and 1000");
  if (
    !Number.isInteger(options.concurrency) ||
    options.concurrency < 1 ||
    options.concurrency > 64
  )
    throw new Error("concurrency must be an integer between 1 and 64");
  if (!originals.length || originals.length * options.repeats > 100000)
    throw new Error("Select 1–100000 total executions");
  if (new Set(originals.map((c) => c.id)).size !== originals.length)
    throw new Error("Duplicate case IDs");
  if (
    options.maxP95Ms !== undefined &&
    (!Number.isFinite(options.maxP95Ms) || options.maxP95Ms < 0)
  )
    throw new Error("Invalid latency gate");
  if (
    options.minPassRate !== undefined &&
    (!Number.isFinite(options.minPassRate) ||
      options.minPassRate < 0 ||
      options.minPassRate > 1)
  )
    throw new Error("Invalid pass rate gate");
  if (
    options.traceBackend &&
    !["none", "langfuse"].includes(options.traceBackend)
  )
    throw new Error("Invalid trace backend");
  const traceConfig =
    options.traceBackend === "langfuse"
      ? (options.traceConfig ?? tracingConfigFromEnv())
      : undefined;
  const cases = originals.map(prepareCase);
  for (const c of cases) {
    if (
      !c.id ||
      !["llm", "embedding", "rerank"].includes(c.task) ||
      !c.input ||
      !Object.keys(c.expect ?? {}).length
    )
      throw new Error(
        "Cases require id, task, input and nonempty expectations",
      );
    if (!skipReason(c)) {
      validateExpectations(c);
      renderInput(c);
    }
    if (
      options.offline &&
      !skipReason(c) &&
      !Object.prototype.hasOwnProperty.call(c, "mock_output") &&
      !c.mock_error
    )
      throw new Error(`No synthetic output: ${c.id}`);
  }
  const providers = options.offline
    ? {}
    : (provided ??
      (await createProviders(
        options.config,
        new Set(cases.filter((c) => !skipReason(c)).map((c) => c.task)),
      )));
  for (const provider of Object.values(providers)) instrumentProvider(provider);
  await mkdir(options.out, { recursive: false }); // Existing runs are never overwritten.
  const mode = options.offline ? "offline_synthetic" : "live";
  const manifest = {
    schema_version: 1,
    run_id: randomUUID(),
    started_at: new Date().toISOString(),
    runtime: "typescript",
    node: process.version,
    mode,
    model_measurement: !options.offline,
    repeats: options.repeats,
    concurrency: options.concurrency,
    config: configMetadata(options.config),
    config_sha256: hash(options.config),
    sources: await sourceMetadata(),
    cases_sha256: hash(originals),
    record_content: !!options.recordContent,
    limits: [
      "Provider replay, not stateful memory evaluation",
      "Keyword probes are not semantic accuracy estimates",
      "SDK method spans omit hidden HTTP retries",
      "Uninstrumented providers have unknown usage and fallback state",
      "Concurrency bounds tasks; embedding batches and LLM rerankers may fan out",
    ],
  };
  await writeFile(
    resolve(options.out, "manifest.json"),
    JSON.stringify(manifest, null, 2) + "\n",
  );
  const jobs = cases.flatMap((c) =>
    Array.from({ length: options.repeats }, (_, repeat) => ({ c, repeat })),
  );
  const records: RecordResult[] = new Array(jobs.length);
  const callsFile = await open(resolve(options.out, "calls.jsonl"), "wx");
  let writes = Promise.resolve();
  const persist = (row: RecordResult): Promise<void> => {
    writes = writes.then(async () => {
      await callsFile.writeFile(JSON.stringify(row) + "\n");
      await callsFile.sync();
    });
    return writes;
  };
  const tracing =
    options.tracing ??
    (traceConfig
      ? new EvaluationTracing({
          ...traceConfig,
          recordContent: !!options.recordContent,
        })
      : undefined);
  let scoreOutbox: ScoreOutbox | undefined;
  let scoreQueueErrors = 0;
  let scoreSetupFailed = false;
  if (traceConfig) {
    try {
      scoreOutbox = await createScoreOutbox(options.out, traceConfig);
    } catch {
      scoreSetupFailed = true;
    }
  }
  const persistAndQueue = async (row: RecordResult) => {
    await persist(row);
    try {
      await scoreOutbox?.enqueue(row);
    } catch {
      scoreQueueErrors++;
    }
  };
  let traceReport;
  let next = 0;
  const started = performance.now();
  let elapsedMs = 0;
  try {
    await Promise.all(
      Array.from(
        { length: Math.min(options.concurrency, jobs.length) },
        async () => {
          while (next < jobs.length) {
            const position = next++;
            const { c, repeat } = jobs[position];
            const skipped = skipReason(c);
            const row: RecordResult = {
              run_id: manifest.run_id,
              call_id: randomUUID(),
              started_at: new Date().toISOString(),
              case_id: c.id,
              task: c.task,
              repeat,
              mode,
              status: "skip",
              latency_ms: null,
              request_sha256: null,
              usage: null,
              provider_attempts: null,
              provenance: c.provenance,
            };
            const root = tracing?.start(
              "mem0.eval.item",
              "span",
              {
                run_id: manifest.run_id,
                call_id: row.call_id,
                case_id: c.id,
                repeat,
                mode,
                task: c.task,
                grader_version: "contract-v1",
                runtime: "typescript",
                provider:
                  options.config[
                    c.task === "llm"
                      ? "llm"
                      : c.task === "embedding"
                        ? "embedder"
                        : "reranker"
                  ]?.provider,
                requested_model:
                  options.config[
                    c.task === "llm"
                      ? "llm"
                      : c.task === "embedding"
                        ? "embedder"
                        : "reranker"
                  ]?.config.model,
                dataset_sha256: manifest.cases_sha256,
                config_sha256: manifest.config_sha256,
                ...manifest.sources,
              },
              true,
            );
            if (root?.span) {
              row.trace_id = root.span.spanContext().traceId;
              row.root_observation_id = root.span.spanContext().spanId;
            }
            records[position] = row;
            if (skipped) {
              row.reason = skipped;
              root?.end("skip");
              await persistAndQueue(row);
              continue;
            }
            const input = renderInput(c);
            row.request_sha256 = hash(input);
            const trace: TraceContext = {
              tracing,
              callId: row.call_id!,
              runId: manifest.run_id,
              caseId: c.id,
              task: c.task,
              sdk: [],
              fallback: false,
            };
            const callStart = performance.now();
            const execute = () =>
              context.run(trace, async () => {
                try {
                  if (options.offline && c.mock_error)
                    throw new Error(c.mock_error);
                  const operation = tracing?.start(
                    `mem0.eval.${c.task}`,
                    "span",
                  );
                  let output: any;
                  try {
                    const call = async () =>
                      options.offline
                        ? structuredClone(c.mock_output)
                        : invoke(c, input, providers);
                    output = operation
                      ? await operation.run(call)
                      : await call();
                    operation?.end("pass");
                  } catch (error) {
                    operation?.end("error");
                    throw error;
                  }
                  const dims = options.offline
                    ? undefined
                    : options.config.embedder?.config.embeddingDims;
                  const grader = tracing?.start("mem0.eval.grade", "evaluator");
                  try {
                    const grading = async () => grade(c, output, dims);
                    row.semantic_failures = grader
                      ? await grader.run(grading)
                      : await grading();
                  } finally {
                    grader?.end();
                  }
                  if (trace.fallback)
                    row.semantic_failures.push("provider_fallback");
                  row.status = row.semantic_failures.length ? "fail" : "pass";
                  row.response_sha256 = hash(output);
                  if (options.recordContent) row.content = { input, output };
                } catch (error) {
                  row.status = "error";
                  row.reason = error instanceof Error ? error.name : "unknown";
                }
              });
            if (root) await root.run(execute);
            else await execute();
            row.latency_ms = performance.now() - callStart;
            row.fallback_detected = options.offline
              ? null
              : trace.fallback
                ? true
                : trace.sdk.length
                  ? false
                  : null;
            row.sdk_spans = trace.sdk;
            root?.end(row.status);
            await persistAndQueue(row);
          }
        },
      ),
    );
  } finally {
    await writes;
    await callsFile.close();
    elapsedMs = performance.now() - started;
    traceReport = await tracing?.shutdown();
  }
  let scoreReport;
  try {
    scoreReport = await scoreOutbox?.flush();
  } catch {
    scoreQueueErrors++;
  }
  const summary: Json = {
    throughput_completions_per_second:
      records.filter((r) => r.status !== "skip").length / (elapsedMs / 1000),
    mode,
    model_measurement: !options.offline,
    ...summarize(records),
    wall_ms: elapsedMs,
    ...(tracing
      ? {
          observability: {
            traces: traceReport,
            scores: scoreReport ?? null,
            score_queue_errors: scoreQueueErrors,
            score_setup_failed: scoreSetupFailed,
          },
        }
      : {}),
    by_task: Object.fromEntries(
      [...new Set(records.map((r) => r.task))].map((task) => [
        task,
        summarize(records.filter((r) => r.task === task)),
      ]),
    ),
    by_case: Object.fromEntries(
      cases.map((c) => [
        c.id,
        summarize(records.filter((r) => r.case_id === c.id)),
      ]),
    ),
    gates: {
      evaluated: !options.offline,
      passed: options.offline ? null : true,
      min_pass_rate: options.minPassRate ?? null,
      max_p95_ms: options.maxP95Ms ?? null,
    },
  };
  if (!options.offline) {
    summary.gates.passed =
      summary.measured > 0 &&
      (options.minPassRate === undefined ||
        (summary.pass_rate ?? 0) >= options.minPassRate) &&
      (options.maxP95Ms === undefined ||
        (summary.latency_ms.p95 ?? Infinity) <= options.maxP95Ms);
  }
  await writeFile(
    resolve(options.out, "summary.json"),
    JSON.stringify(summary, null, 2) + "\n",
  );
  return summary;
}
export async function main(argv: string[]): Promise<number> {
  const values: Json = {};
  const flags = new Set(["--offline", "--record-content"]);
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--help") {
      console.log(
        "evaluate_ai_calls.ts --cases FILE --out NEW_DIR [--offline | --config FILE] [--task llm|embedding|rerank] [--case ID] [--repeats 1..1000] [--concurrency 1..64] [--min-pass-rate 0..1] [--max-p95-ms N] [--record-content] [--trace-backend none|langfuse]",
      );
      return 0;
    }
    if (
      ![
        ...flags,
        "--cases",
        "--out",
        "--config",
        "--task",
        "--case",
        "--repeats",
        "--concurrency",
        "--min-pass-rate",
        "--max-p95-ms",
        "--trace-backend",
      ].includes(arg)
    )
      throw new Error(`Unknown argument: ${arg}`);
    values[arg] = flags.has(arg) ? true : argv[++i];
    if (values[arg] === undefined)
      throw new Error(`Missing argument value: ${arg}`);
  }
  if (
    !values["--cases"] ||
    !values["--out"] ||
    (!values["--offline"] && !values["--config"])
  )
    throw new Error("Require --cases, --out, and --offline or --config");
  const cases = (await readFile(values["--cases"], "utf8"))
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line))
    .filter(
      (c) =>
        (!values["--task"] || c.task === values["--task"]) &&
        (!values["--case"] || c.id === values["--case"]),
    );
  const config = values["--config"]
    ? resolveEnv(JSON.parse(await readFile(values["--config"], "utf8")))
    : {};
  const summary = await runEvaluation(cases, {
    traceBackend: values["--trace-backend"] ?? "none",
    offline: !!values["--offline"],
    out: resolve(values["--out"]),
    config,
    repeats: Number(values["--repeats"] ?? 1),
    concurrency: Number(values["--concurrency"] ?? 1),
    minPassRate:
      values["--min-pass-rate"] === undefined
        ? undefined
        : Number(values["--min-pass-rate"]),
    maxP95Ms:
      values["--max-p95-ms"] === undefined
        ? undefined
        : Number(values["--max-p95-ms"]),
    recordContent: !!values["--record-content"],
  });
  console.log(JSON.stringify(summary, null, 2));
  return summary.failed || summary.errors || summary.gates.passed === false
    ? 1
    : 0;
}
// Compatible with both source ts-node execution and import by Jest.
if (/evaluate_ai_calls\.(?:ts|js)$/.test(process.argv[1] ?? "")) {
  main(process.argv.slice(2))
    .then((code) => {
      process.exitCode = code;
    })
    .catch((error) => {
      console.error(
        error instanceof Error
          ? `Evaluation setup failed (${error.name}); check configuration, installed model names, and output directory.`
          : "Evaluation failed",
      );
      process.exitCode = 2;
    });
}
