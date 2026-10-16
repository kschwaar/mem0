import * as fs from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  createScoreOutbox,
  ScoreOutcome,
  ScoreTransport,
  scoreConfigFromEnv,
} from "../scripts/evaluation_scores";
const config = {
  baseUrl: "http://localhost:4000",
  publicKey: "public-test",
  secretKey: "secret-test",
  timeoutMs: 10,
  maxAttempts: 2,
};
const outcome: ScoreOutcome = {
  run_id: "run",
  case_id: "case",
  repeat: 0,
  mode: "offline_synthetic",
  status: "pass",
  trace_id: "1".repeat(32),
  root_observation_id: "2".repeat(16),
};
let dir: string;
beforeEach(async () => {
  dir = await fs.mkdtemp(join(tmpdir(), "score-tests-"));
});
afterEach(async () => {
  await fs.rm(dir, { recursive: true, force: true });
});
test("durable concurrent enqueue, duplicates, linkage, privacy and replay", async () => {
  const sent: any[] = [];
  const send: ScoreTransport = async (_, init) => {
    sent.push(JSON.parse(init.body as string));
    return { ok: false, status: 503 };
  };
  const a = await createScoreOutbox(dir, config, send),
    b = await createScoreOutbox(dir, config, send);
  await Promise.all(
    Array.from({ length: 10 }, (_, n) =>
      (n % 2 ? a : b).enqueue({
        ...outcome,
        semantic_failures: ["PRIVATE RAW MODEL"],
      }),
    ),
  );
  expect(sent).toHaveLength(0);
  expect(await a.flush()).toMatchObject({
    queued: 2,
    pending: 2,
    delivered: 0,
    failedAttempts: 4,
  });
  const replayed: any[] = [];
  const replay = await createScoreOutbox(dir, config, async (_, init) => {
    replayed.push(JSON.parse(init.body as string));
    return { ok: true, status: 201 };
  });
  expect(await replay.flush()).toMatchObject({
    queued: 2,
    pending: 0,
    delivered: 2,
  });
  expect(replayed.map((s) => s.id)).toEqual([sent[0].id, sent[2].id]);
  expect(replayed[0].observationId).toBe(outcome.root_observation_id);
  expect(await replay.flush()).toMatchObject({ pending: 0, delivered: 0 });
  expect(
    await fs.readFile(join(dir, "scores-outbox.jsonl"), "utf8"),
  ).not.toMatch(/PRIVATE|secret-test|public-test|case/);
});
test("skip and unknown do not invent boolean scores; unlinked outcomes export nothing", async () => {
  const values: any[] = [];
  const box = await createScoreOutbox(dir, config, async (_, init) => {
    values.push(JSON.parse(init.body as string));
    return { ok: true, status: 200 };
  });
  await box.enqueue({ ...outcome, status: "skip", fallback_detected: true });
  await box.enqueue({
    ...outcome,
    repeat: 1,
    status: "error",
    fallback_detected: null,
  });
  await box.enqueue({
    ...outcome,
    trace_id: undefined,
    root_observation_id: undefined,
  });
  expect(await box.flush()).toMatchObject({
    queued: 2,
    delivered: 2,
    unlinked: 1,
  });
  expect(values.map((s) => s.name)).toEqual(["trial_status", "trial_status"]);
});
test("timeouts and transport exceptions remain pending with safe codes", async () => {
  const box = await createScoreOutbox(dir, config, async () => {
    throw Error("secret-test private response");
  });
  await box.enqueue(outcome);
  expect(await box.flush()).toMatchObject({
    pending: 2,
    failedAttempts: 4,
    errorCodes: ["DELIVERY_NETWORK"],
  });
  const slow = await createScoreOutbox(
    dir,
    config,
    async () => new Promise(() => {}),
  );
  expect(await slow.flush()).toMatchObject({
    pending: 2,
    failedAttempts: 4,
    errorCodes: ["DELIVERY_TIMEOUT"],
  });
});
test("malformed and partial journals preserve evidence and safe diagnostics", async () => {
  const path = join(dir, "scores-outbox.jsonl");
  await fs.writeFile(path, '{"secret":"private"}\n');
  await expect(createScoreOutbox(dir, config)).rejects.toMatchObject({
    code: "OUTBOX_MALFORMED",
    message: "OUTBOX_MALFORMED",
  });
  await fs.writeFile(path, '{"type":');
  await expect(createScoreOutbox(dir, config)).rejects.toMatchObject({
    code: "OUTBOX_PARTIAL_WRITE",
  });
  expect(await fs.readFile(path, "utf8")).toBe('{"type":');
});
test("abandoned process lock recovery and conflicting IDs", async () => {
  await fs.writeFile(join(dir, "scores-outbox.lock"), "2147483647");
  const box = await createScoreOutbox(dir, config);
  await box.enqueue(outcome);
  await expect(
    box.enqueue({ ...outcome, status: "fail" }),
  ).rejects.toMatchObject({ code: "OUTBOX_CONFLICT" });
});
test("bounded config and linkage validation", async () => {
  expect(() => scoreConfigFromEnv({})).toThrow("INVALID_CONFIG");
  await expect(
    createScoreOutbox(dir, {
      ...config,
      baseUrl: "http://user:password@localhost",
    }),
  ).rejects.toThrow("INVALID_CONFIG");
  await expect(
    createScoreOutbox(dir, { ...config, maxAttempts: 100 }),
  ).rejects.toThrow("INVALID_CONFIG");
  const box = await createScoreOutbox(dir, config);
  await expect(box.enqueue({ ...outcome, trace_id: "bad" })).rejects.toThrow(
    "INVALID_LINKAGE",
  );
});
test("permanent HTTP failures stay pending without retries", async () => {
  const send = jest.fn(async () => ({ ok: false, status: 401 }));
  const box = await createScoreOutbox(dir, config, send);
  await box.enqueue(outcome);
  expect(await box.flush()).toMatchObject({ pending: 2, failedAttempts: 2 });
  expect(send).toHaveBeenCalledTimes(2);
});

test("enqueue can continue during delivery and reports the latest pending journal", async () => {
  let entered!: () => void;
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  let resume!: () => void;
  const wait = new Promise<void>((resolve) => {
    resume = resolve;
  });
  let calls = 0;
  const box = await createScoreOutbox(
    dir,
    { ...config, timeoutMs: 1000 },
    async () => {
      if (++calls === 1) {
        entered();
        await wait;
      }
      return { ok: true, status: 200 };
    },
  );
  await box.enqueue(outcome);
  const delivery = box.flush();
  await started;
  await box.enqueue({ ...outcome, repeat: 1 });
  resume();
  expect(await delivery).toMatchObject({ queued: 4, delivered: 2, pending: 2 });
  expect(await box.flush()).toMatchObject({
    queued: 4,
    delivered: 2,
    pending: 0,
  });
});

test("interrupted temporary replacement does not corrupt committed queue; partial evidence can be repaired then replayed", async () => {
  const box = await createScoreOutbox(dir, config);
  await box.enqueue(outcome);
  const path = join(dir, "scores-outbox.jsonl"),
    original = await fs.readFile(path, "utf8");
  await fs.writeFile(join(dir, ".scores-interrupted.tmp"), '{"type":');
  const send = jest.fn(async () => ({ ok: true, status: 200 }));
  const recovered = await createScoreOutbox(dir, config, send);
  expect(await recovered.flush()).toMatchObject({ delivered: 2, pending: 0 });
  await fs.writeFile(path, original + '{"type":');
  await expect(createScoreOutbox(dir, config, send)).rejects.toMatchObject({
    code: "OUTBOX_PARTIAL_WRITE",
  });
  expect(send).toHaveBeenCalledTimes(2);
  await fs.writeFile(path, original);
  const repaired = await createScoreOutbox(dir, config, send);
  expect(await repaired.flush()).toMatchObject({ delivered: 2, pending: 0 });
});

test("flush caps work at 100 scores and leaves the remainder durable", async () => {
  const send = jest.fn(async () => ({ ok: true, status: 200 }));
  const box = await createScoreOutbox(dir, config, send);
  for (let repeat = 0; repeat < 51; repeat++)
    await box.enqueue({ ...outcome, repeat });
  expect(await box.flush()).toMatchObject({
    queued: 102,
    delivered: 100,
    pending: 2,
  });
  expect(send).toHaveBeenCalledTimes(100);
  expect(await box.flush()).toMatchObject({
    queued: 102,
    delivered: 2,
    pending: 0,
  });
});
