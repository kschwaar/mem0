import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ADDITIVE_EXTRACTION_PROMPT,
  generateAdditiveExtractionPrompt,
} from "../src/oss/src/prompts";
import {
  EvalCase,
  grade,
  prepareCase,
  redact,
  renderInput,
  resolveEnv,
  runEvaluation,
  summarize,
} from "../scripts/evaluate_ai_calls";
import { LLMReranker } from "../src/oss/src/rerankers/llm";
const fixture: EvalCase = {
  id: "fact",
  task: "llm",
  input: {
    stage: "additive_extraction",
    prompt_args: {
      new_messages: "user: I drink tea.",
      existing_memories: [],
      current_date: "2026-10-05",
      timestamp: "2026-10-04",
    },
    response_format: { type: "json_object" },
  },
  expect: {
    additive_schema: true,
    memory_items: [{ contains: ["tea"], attributed_to: "user" }],
  },
  mock_output:
    '{"memory":[{"id":"0","text":"User drinks tea.","attributed_to":"user"}]}',
};
let temporary: string;
beforeEach(async () => {
  temporary = await mkdtemp(join(tmpdir(), "mem0-ts-eval-test-"));
});
afterEach(async () => {
  await rm(temporary, { recursive: true, force: true });
});
test("renders exact TypeScript production prompts rather than Python action contract", () => {
  const result = renderInput(fixture);
  expect(result.messages).toEqual([
    { role: "system", content: ADDITIVE_EXTRACTION_PROMPT },
    {
      role: "user",
      content: generateAdditiveExtractionPrompt({
        newMessages: "user: I drink tea.",
        existingMemories: [],
        currentDate: "2026-10-05",
        observationDate: "2026-10-04",
      }),
    },
  ]);
  expect(result.stage).toBeUndefined();
  expect(result.messages[0].content).not.toContain(
    "The action rules below override",
  );
});
test("valid JSON missing facts or with wrong attribution does not pass semantic probes", () => {
  expect(grade(fixture, '{"memory":[]}')).not.toEqual([]);
  expect(
    grade(
      fixture,
      '{"memory":[{"id":"0","text":"User drinks tea.","attributed_to":"assistant"}]}',
    ),
  ).not.toEqual([]);
  expect(grade(fixture, "{garbled}")).toEqual(["invalid_json"]);
  expect(grade(fixture, fixture.mock_output)).toEqual([]);
});
test("rejects nonfinite, empty, wrong-count and wrong-dimension embeddings", () => {
  const c: EvalCase = {
    id: "vec",
    task: "embedding",
    input: { texts: ["a", "b"] },
    expect: { finite_vectors: true },
  };
  expect(
    grade(
      c,
      [
        [1, 2],
        [3, 4],
      ],
      2,
    ),
  ).toEqual([]);
  expect(grade(c, [[1, 2]], 2)).toContain("vector_count");
  expect(
    grade(
      c,
      [
        [NaN, 2],
        [3, 4],
      ],
      2,
    ),
  ).toContain("finite_vector");
  expect(grade(c, [[], [3, 4]], 2)).toContain("finite_vector");
  expect(
    grade(
      c,
      [
        [1, 2],
        [3, 4],
      ],
      768,
    ),
  ).toContain("vector_dimensions");
});
test("offline is synthetic, skips never count as passed and gates are disabled", async () => {
  const skipped = {
    ...fixture,
    id: "relationship",
    input: {
      stage: "relationship_extraction",
      memory_text: "Alice works for Acme.",
    },
  };
  const summary = await runEvaluation([fixture, skipped], {
    offline: true,
    repeats: 2,
    concurrency: 2,
    out: join(temporary, "run"),
    config: {},
    maxP95Ms: 0,
    minPassRate: 1,
  });
  expect(summary).toMatchObject({
    mode: "offline_synthetic",
    model_measurement: false,
    total: 4,
    measured: 2,
    passed: 2,
    skipped: 2,
    gates: { evaluated: false, passed: null },
  });
  const manifest = JSON.parse(
    await readFile(join(temporary, "run", "manifest.json"), "utf8"),
  );
  expect(manifest.model_measurement).toBe(false);
  const records = (
    await readFile(join(temporary, "run", "calls.jsonl"), "utf8")
  )
    .trim()
    .split("\n")
    .map((l) => JSON.parse(l));
  expect(
    records.every(
      (r) => r.usage === null && r.provider_attempts === null && !r.content,
    ),
  ).toBe(true);
  expect(records.find((r) => r.status === "skip").latency_ms).toBeNull();
});
test("bounds provider concurrency and retains errors without exposing messages", async () => {
  let active = 0;
  let maximum = 0;
  let calls = 0;
  const llm = {
    generateChat: jest.fn(),
    generateResponse: async () => {
      const n = calls++;
      active++;
      maximum = Math.max(maximum, active);
      await new Promise((r) => setTimeout(r, 8));
      active--;
      if (n === 0) throw new Error("sensitive provider response");
      return fixture.mock_output;
    },
  };
  const summary = await runEvaluation(
    [fixture],
    {
      offline: false,
      repeats: 8,
      concurrency: 2,
      out: join(temporary, "run"),
      config: {},
    },
    { llm },
  );
  expect(maximum).toBe(2);
  expect(summary.errors).toBe(1);
  expect(summary.measured).toBe(8);
  expect(
    await readFile(join(temporary, "run", "calls.jsonl"), "utf8"),
  ).not.toContain("sensitive provider response");
});
test("real reranker swallowing provider failure fails even when target fallback ranks first", async () => {
  const llm = {
    generateChat: jest.fn(),
    generateResponse: async () => {
      throw new Error("model unavailable");
    },
  };
  const reranker = new LLMReranker({}, llm);
  const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
  try {
    const c: EvalCase = {
      id: "rank",
      task: "rerank",
      input: {
        query: "drink",
        documents: [{ id: "tea", memory: "Tea" }],
        top_k: 1,
      },
      expect: { top_id: "tea" },
    };
    const summary = await runEvaluation(
      [c],
      {
        offline: false,
        repeats: 1,
        concurrency: 1,
        out: join(temporary, "run"),
        config: {},
      },
      { reranker },
    );
    expect(summary).toMatchObject({ passed: 0, failed: 1, fallback_count: 1 });
    expect(
      await readFile(join(temporary, "run", "calls.jsonl"), "utf8"),
    ).toContain("provider_fallback");
  } finally {
    warn.mockRestore();
  }
});
test("captures SDK usage before normalization without retaining raw response content", async () => {
  const create = jest.fn().mockResolvedValue({
    usage: { prompt_tokens: 12, completion_tokens: 8 },
    secret: "raw-response",
  });
  const llm = {
    openai: { chat: { completions: { create } } },
    generateChat: jest.fn(),
    generateResponse: async function () {
      await this.openai.chat.completions.create();
      return fixture.mock_output;
    },
  };
  const summary = await runEvaluation(
    [fixture],
    {
      offline: false,
      repeats: 1,
      concurrency: 1,
      out: join(temporary, "run"),
      config: {},
    },
    { llm },
  );
  expect(summary.observed_sdk_calls).toBe(1);
  const text = await readFile(join(temporary, "run", "calls.jsonl"), "utf8");
  expect(text).toContain('"prompt_tokens":12');
  expect(text).not.toContain("raw-response");
});
test("redacts nested secrets and URL credentials and rejects missing env placeholders", () => {
  expect(
    redact({
      llm: {
        config: {
          apiKey: "secret",
          baseURL: "https://user:pass@server/path?token=secret",
          headers: { Authorization: "secret" },
        },
      },
    }),
  ).toEqual({
    llm: {
      config: {
        apiKey: "[redacted]",
        baseURL: "https://server/path",
        headers: "[redacted]",
      },
    },
  });
  expect(() => resolveEnv("${MEM0_EVAL_MISSING_TEST_VARIABLE}")).toThrow(
    "Missing environment variable",
  );
});
test("TypeScript fixture overrides replace Python expectations explicitly", () => {
  const c = prepareCase({
    ...fixture,
    expect: { memory_items: [{ event: "ADD" }] },
    typescript: { expect: fixture.expect, mock_output: fixture.mock_output },
  });
  expect(grade(c, fixture.mock_output)).toEqual([]);
});
test("all skipped means unknown quality and latency, never a perfect score", () => {
  expect(
    summarize([
      {
        case_id: "skip",
        task: "llm",
        repeat: 0,
        mode: "live",
        status: "skip",
        latency_ms: null,
        request_sha256: null,
        usage: null,
        provider_attempts: null,
      },
    ]),
  ).toMatchObject({
    measured: 0,
    passed: 0,
    pass_rate: null,
    latency_ms: { p95: null },
  });
});
test("invalid bounds fail before artifacts and existing runs cannot be overwritten", async () => {
  const opts = {
    offline: true,
    repeats: 1,
    concurrency: 0,
    out: join(temporary, "run"),
    config: {},
  };
  await expect(runEvaluation([fixture], opts)).rejects.toThrow("concurrency");
  await runEvaluation([fixture], { ...opts, concurrency: 1 });
  await expect(
    runEvaluation([fixture], { ...opts, concurrency: 1 }),
  ).rejects.toThrow();
});

test("unknown or vacuous assertions fail before artifacts, and tool names require an exact match", async () => {
  const opts = {
    offline: true,
    repeats: 1,
    concurrency: 1,
    out: join(temporary, "run"),
    config: {},
  };
  for (const expectValue of [
    { typo: true },
    { json_required: false },
    { json_type: "banana" },
    { contains: [] },
    { memory_items: [{}] },
  ]) {
    await expect(
      runEvaluation([{ ...fixture, expect: expectValue }], opts),
    ).rejects.toThrow();
  }
  const toolCase: EvalCase = {
    id: "tool",
    task: "llm",
    input: { messages: [] },
    expect: { tool_names: [] },
  };
  expect(grade(toolCase, { toolCalls: [{ name: "unexpected" }] })).toContain(
    "tool_names",
  );
  expect(grade(toolCase, { toolCalls: [] })).toEqual([]);
});
test("safe manifest metadata omits arbitrary config secrets while original config hashes distinguish changes", async () => {
  const config = {
    llm: {
      provider: "openai",
      config: {
        model: "test-model",
        privateNote: "unknown-secret",
        apiKey: "known-secret",
        baseURL: "http://private.internal",
      },
    },
  };
  await runEvaluation([fixture], {
    offline: true,
    repeats: 1,
    concurrency: 1,
    out: join(temporary, "first"),
    config,
  });
  await runEvaluation([fixture], {
    offline: true,
    repeats: 1,
    concurrency: 1,
    out: join(temporary, "second"),
    config: {
      ...config,
      llm: {
        ...config.llm,
        config: { ...config.llm.config, privateNote: "changed" },
      },
    },
  });
  const text = await readFile(
    join(temporary, "first", "manifest.json"),
    "utf8",
  );
  expect(text).not.toContain("unknown-secret");
  expect(text).not.toContain("known-secret");
  expect(text).not.toContain("private.internal");
  const first = JSON.parse(text);
  const second = JSON.parse(
    await readFile(join(temporary, "second", "manifest.json"), "utf8"),
  );
  expect(first.config_sha256).not.toBe(second.config_sha256);
  expect(first.sources.git_sha).toMatch(/^[0-9a-f]{40}$/);
  expect(first.sources.script_sha256).toMatch(/^[0-9a-f]{64}$/);
});
test("completed rows are fsynced before another in-flight provider completes", async () => {
  let release!: () => void;
  let notify!: () => void;
  let n = 0;
  const waiting = new Promise<void>((r) => {
    release = r;
  });
  const entered = new Promise<void>((r) => {
    notify = r;
  });
  const llm = {
    generateChat: jest.fn(),
    generateResponse: async () => {
      if (n++ === 1) {
        notify();
        await waiting;
      }
      return fixture.mock_output;
    },
  };
  const work = runEvaluation(
    [fixture],
    {
      offline: false,
      repeats: 2,
      concurrency: 2,
      out: join(temporary, "run"),
      config: {},
    },
    { llm },
  );
  await entered;
  let rows = "";
  try {
    for (let i = 0; i < 100; i++) {
      rows = await readFile(join(temporary, "run", "calls.jsonl"), "utf8");
      if (rows.trim()) break;
      await new Promise((r) => setTimeout(r, 2));
    }
    expect(rows.trim().split("\n")).toHaveLength(1);
  } finally {
    release();
    await work;
  }
});

test("real OpenAI adapter receives rendered TS prompts and exposes SDK spans before normalization", async () => {
  const { OpenAILLM } = await import("../src/oss/src/llms/openai");
  const llm = new OpenAILLM({
    apiKey: "synthetic-placeholder",
    model: "stub-qwen",
    baseURL: "http://127.0.0.1:1/v1",
  });
  const create = jest.fn().mockResolvedValue({
    model: "served-qwen",
    usage: { prompt_tokens: 12, completion_tokens: 8 },
    choices: [{ message: { content: fixture.mock_output, role: "assistant" } }],
  });
  (llm as any).openai.chat.completions.create = create;
  await runEvaluation(
    [fixture],
    {
      offline: false,
      repeats: 1,
      concurrency: 1,
      out: join(temporary, "run"),
      config: {},
    },
    { llm },
  );
  expect(create).toHaveBeenCalledWith(
    expect.objectContaining({
      model: "stub-qwen",
      messages: renderInput(fixture).messages,
      response_format: { type: "json_object" },
    }),
  );
  const row = JSON.parse(
    (await readFile(join(temporary, "run", "calls.jsonl"), "utf8")).trim(),
  );
  expect(row.sdk_spans[0]).toMatchObject({
    response_model: "served-qwen",
    case_id: "fact",
    run_id: row.run_id,
    parent_call_id: row.call_id,
  });
  expect(row.sdk_spans[0].span_id).toEqual(expect.any(String));
  expect(row.sdk_spans[0].started_at).toEqual(expect.any(String));
});
