import { createHash, randomUUID } from "node:crypto";
import * as fs from "node:fs/promises";
import { resolve, join } from "node:path";

export interface ScoreOutcome {
  run_id?: string;
  call_id?: string;
  case_id: string;
  repeat: number;
  mode: "offline_synthetic" | "live";
  status: "pass" | "fail" | "error" | "skip";
  trace_id?: string;
  root_observation_id?: string;
  semantic_failures?: string[];
  fallback_detected?: boolean | null;
}
export interface LangfuseScoreConfig {
  baseUrl: string;
  publicKey: string;
  secretKey: string;
  timeoutMs?: number;
  maxAttempts?: number;
}
export interface ScoreDeliveryReport {
  queued: number;
  delivered: number;
  pending: number;
  failedAttempts: number;
  errorCodes: string[];
  unlinked: number;
}
export class ScoreOutboxError extends Error {
  constructor(public readonly code: string) {
    super(code);
    this.name = "ScoreOutboxError";
  }
}
export interface ScoreOutbox {
  enqueue(outcome: ScoreOutcome): Promise<void>;
  flush(): Promise<ScoreDeliveryReport>;
}
export type ScoreTransport = (
  url: string,
  init: RequestInit,
) => Promise<
  Pick<Response, "ok" | "status"> & {
    body?: { cancel(): Promise<void> } | null;
  }
>;
interface Score {
  id: string;
  traceId: string;
  observationId?: string;
  name: "trial_status" | "contract_pass" | "fallback_detected";
  dataType: "BOOLEAN" | "CATEGORICAL";
  value: number | string;
  comment: "live" | "offline_synthetic";
}
type RecordEntry =
  | { type: "score"; score: Score }
  | { type: "ack"; id: string }
  | { type: "unlinked"; id: string };
const LIMIT = 16 * 1024 * 1024;
const fail = (code: string): never => {
  throw new ScoreOutboxError(code);
};
function bounded(value: unknown, max = 256): value is string {
  return (
    typeof value === "string" && value.trim().length > 0 && value.length <= max
  );
}
function configValue(config: LangfuseScoreConfig) {
  let url: URL;
  try {
    url = new URL(config.baseUrl);
  } catch {
    return fail("INVALID_CONFIG");
  }
  if (
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/" ||
    !bounded(config.publicKey, 1024) ||
    !bounded(config.secretKey, 4096) ||
    /[\r\n:]/.test(config.publicKey) ||
    /[\r\n]/.test(config.secretKey)
  )
    fail("INVALID_CONFIG");
  const timeout = config.timeoutMs ?? 5000,
    attempts = config.maxAttempts ?? 3;
  if (
    !Number.isInteger(timeout) ||
    timeout < 1 ||
    timeout > 30000 ||
    !Number.isInteger(attempts) ||
    attempts < 1 ||
    attempts > 5
  )
    fail("INVALID_CONFIG");
  return {
    endpoint: new URL("/api/public/scores", url).href,
    timeout,
    attempts,
    auth: `Basic ${Buffer.from(`${config.publicKey}:${config.secretKey}`).toString("base64")}`,
  };
}
function scores(outcome: ScoreOutcome): Score[] {
  if (
    !bounded(outcome.case_id) ||
    !Number.isSafeInteger(outcome.repeat) ||
    outcome.repeat < 0 ||
    !["live", "offline_synthetic"].includes(outcome.mode) ||
    !["pass", "fail", "error", "skip"].includes(outcome.status) ||
    [outcome.run_id, outcome.call_id].some(
      (v) => v !== undefined && !bounded(v),
    ) ||
    (outcome.fallback_detected != null &&
      typeof outcome.fallback_detected !== "boolean")
  )
    fail("INVALID_OUTCOME");
  if (outcome.trace_id === undefined) {
    if (outcome.root_observation_id !== undefined) fail("INVALID_LINKAGE");
    return [];
  }
  if (
    !/^[a-f0-9]{32}$/.test(outcome.trace_id) ||
    /^0+$/.test(outcome.trace_id) ||
    (outcome.root_observation_id !== undefined &&
      (!/^[a-f0-9]{16}$/.test(outcome.root_observation_id) ||
        /^0+$/.test(outcome.root_observation_id)))
  )
    fail("INVALID_LINKAGE");
  const make = (
    name: Score["name"],
    value: Score["value"],
    dataType: Score["dataType"],
  ): Score => ({
    id: createHash("sha256")
      .update(
        JSON.stringify([
          "evaluation-v1",
          outcome.run_id ?? "",
          outcome.case_id,
          outcome.repeat,
          outcome.call_id ?? "",
          name,
          outcome.trace_id,
          outcome.root_observation_id ?? "",
        ]),
      )
      .digest("hex"),
    traceId: outcome.trace_id!,
    ...(outcome.root_observation_id
      ? { observationId: outcome.root_observation_id }
      : {}),
    name,
    value,
    dataType,
    comment: outcome.mode,
  });
  const result = [make("trial_status", outcome.status, "CATEGORICAL")];
  if (outcome.status === "pass" || outcome.status === "fail")
    result.push(
      make("contract_pass", outcome.status === "pass" ? 1 : 0, "BOOLEAN"),
    );
  if (
    outcome.status !== "skip" &&
    typeof outcome.fallback_detected === "boolean"
  )
    result.push(
      make("fallback_detected", Number(outcome.fallback_detected), "BOOLEAN"),
    );
  return result;
}
function validScore(s: Score): boolean {
  return (
    !!s &&
    /^[a-f0-9]{64}$/.test(s.id) &&
    /^[a-f0-9]{32}$/.test(s.traceId) &&
    !/^0+$/.test(s.traceId) &&
    (s.observationId === undefined ||
      (/^[a-f0-9]{16}$/.test(s.observationId) &&
        !/^0+$/.test(s.observationId))) &&
    ["live", "offline_synthetic"].includes(s.comment) &&
    (s.name === "trial_status"
      ? s.dataType === "CATEGORICAL" &&
        ["pass", "fail", "error", "skip"].includes(String(s.value))
      : ["contract_pass", "fallback_detected"].includes(s.name) &&
        s.dataType === "BOOLEAN" &&
        (s.value === 0 || s.value === 1)) &&
    Object.keys(s).every((k) =>
      [
        "id",
        "traceId",
        "observationId",
        "name",
        "value",
        "dataType",
        "comment",
      ].includes(k),
    )
  );
}
export async function createScoreOutbox(
  outputDir: string,
  config: LangfuseScoreConfig,
  transport: ScoreTransport = fetch,
): Promise<ScoreOutbox> {
  const settings = configValue(config);
  if (!bounded(outputDir, 4096)) fail("INVALID_DIRECTORY");
  const dir = resolve(outputDir),
    journal = join(dir, "scores-outbox.jsonl"),
    lock = join(dir, "scores-outbox.lock");
  try {
    await fs.mkdir(dir, { recursive: true, mode: 0o700 });
  } catch {
    fail("OUTBOX_IO");
  }
  async function locked<T>(fn: () => Promise<T>): Promise<T> {
    let handle: fs.FileHandle | undefined;
    for (let n = 0; n < 200; n++) {
      try {
        handle = await fs.open(lock, "wx", 0o600);
        await handle.writeFile(String(process.pid));
        await handle.sync();
        break;
      } catch (e) {
        if ((e as NodeJS.ErrnoException).code !== "EEXIST") {
          if (handle) {
            await handle.close().catch(() => {});
            await fs.unlink(lock).catch(() => {});
          }
          fail("OUTBOX_IO");
        }
        // Serialize recovery: a stale contender must not unlink a new owner's lock.
        let recovery: fs.FileHandle | undefined;
        const recoveryPath = lock + ".recovery";
        try {
          recovery = await fs.open(recoveryPath, "wx", 0o600);
          const pid = Number(await fs.readFile(lock, "utf8"));
          if (Number.isInteger(pid) && pid > 0) {
            try {
              process.kill(pid, 0);
            } catch (err) {
              if ((err as NodeJS.ErrnoException).code === "ESRCH")
                await fs.unlink(lock);
            }
          } else if (Date.now() - (await fs.stat(lock)).mtimeMs > 30000)
            await fs.unlink(lock);
        } catch {
          /* Recovery is busy or the lock was released. */
        } finally {
          if (recovery) {
            await recovery.close().catch(() => {});
            await fs.unlink(recoveryPath).catch(() => {});
          }
        }
        await new Promise((r) => setTimeout(r, 10));
      }
    }
    if (!handle) {
      let recovering = false;
      try {
        await fs.stat(lock + ".recovery");
        recovering = true;
      } catch {}
      fail(recovering ? "OUTBOX_RECOVERY_REQUIRED" : "OUTBOX_BUSY");
    }
    try {
      return await fn();
    } catch (e) {
      if (e instanceof ScoreOutboxError) throw e;
      return fail("OUTBOX_IO");
    } finally {
      await handle!.close().catch(() => {});
      await fs.unlink(lock).catch(() => {});
    }
  }
  async function read(): Promise<RecordEntry[]> {
    let data: string;
    try {
      const stat = await fs.stat(journal);
      if (stat.size > LIMIT) fail("OUTBOX_LIMIT");
      data = await fs.readFile(journal, "utf8");
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw e;
    }
    if (data && !data.endsWith("\n")) fail("OUTBOX_PARTIAL_WRITE");
    const records: RecordEntry[] = [];
    const known = new Map<string, string>();
    for (const line of data.split("\n").slice(0, -1)) {
      let entry: RecordEntry;
      try {
        entry = JSON.parse(line);
      } catch {
        return fail("OUTBOX_MALFORMED");
      }
      if (!entry || typeof entry !== "object") fail("OUTBOX_MALFORMED");
      if (
        entry.type === "score" &&
        Object.keys(entry).length === 2 &&
        validScore(entry.score)
      ) {
        const serialized = JSON.stringify(entry.score);
        if (
          known.has(entry.score.id) &&
          known.get(entry.score.id) !== serialized
        )
          fail("OUTBOX_CONFLICT");
        known.set(entry.score.id, serialized);
      } else if (
        entry.type === "unlinked" &&
        Object.keys(entry).length === 2 &&
        /^[a-f0-9]{64}$/.test(entry.id)
      ) {
        // A durable local diagnostic, never a remote score.
      } else if (
        !(
          entry.type === "ack" &&
          Object.keys(entry).length === 2 &&
          known.has(entry.id)
        )
      )
        fail("OUTBOX_MALFORMED");
      records.push(entry);
    }
    return records;
  }
  async function write(records: RecordEntry[]) {
    const data = records.map((r) => JSON.stringify(r) + "\n").join("");
    if (Buffer.byteLength(data) > LIMIT) fail("OUTBOX_LIMIT");
    const temp = join(dir, `.scores-${randomUUID()}.tmp`);
    try {
      const h = await fs.open(temp, "wx", 0o600);
      try {
        await h.writeFile(data);
        await h.sync();
      } finally {
        await h.close();
      }
      await fs.rename(temp, journal);
      const d = await fs.open(dir, "r");
      try {
        await d.sync();
      } finally {
        await d.close();
      }
    } catch (e) {
      if (e instanceof ScoreOutboxError) throw e;
      fail("OUTBOX_IO");
    } finally {
      await fs.unlink(temp).catch(() => {});
    }
  }
  await locked(read);
  return {
    async enqueue(outcome) {
      const values = scores(outcome);
      await locked(async () => {
        const records = await read();
        const known = new Map(
          records
            .filter(
              (r): r is { type: "score"; score: Score } => r.type === "score",
            )
            .map((r) => [r.score.id, JSON.stringify(r.score)]),
        );
        for (const score of values) {
          if (known.has(score.id)) {
            if (known.get(score.id) !== JSON.stringify(score))
              fail("OUTBOX_CONFLICT");
          } else {
            records.push({ type: "score", score });
            known.set(score.id, JSON.stringify(score));
          }
        }
        if (!values.length) {
          const id = createHash("sha256")
            .update(
              JSON.stringify([
                "unlinked-v1",
                outcome.run_id ?? "",
                outcome.call_id ?? "",
                outcome.case_id,
                outcome.repeat,
                outcome.mode,
              ]),
            )
            .digest("hex");
          if (!records.some((r) => r.type === "unlinked" && r.id === id))
            records.push({ type: "unlinked", id });
        }
        await write(records);
      });
    },
    async flush() {
      const records = await locked(read);
      {
        const acknowledged = new Set(
          records
            .filter((r): r is { type: "ack"; id: string } => r.type === "ack")
            .map((r) => r.id),
        );
        const all = new Map(
          records
            .filter(
              (r): r is { type: "score"; score: Score } => r.type === "score",
            )
            .map((r) => [r.score.id, r.score]),
        );
        const pending = [...all.values()].filter(
          (s) => !acknowledged.has(s.id),
        );
        const report: ScoreDeliveryReport = {
          queued: all.size,
          delivered: 0,
          pending: pending.length,
          failedAttempts: 0,
          errorCodes: [],
          unlinked: new Set(
            records
              .filter((r) => r.type === "unlinked")
              .map((r) => (r as { type: "unlinked"; id: string }).id),
          ).size,
        };
        const deadline = Date.now() + 30000;
        for (const score of pending.slice(0, 100)) {
          if (Date.now() >= deadline) break;
          for (let attempt = 0; attempt < settings.attempts; attempt++) {
            if (Date.now() >= deadline) break;
            const controller = new AbortController();
            let timer: ReturnType<typeof setTimeout> | undefined;
            try {
              const response = await Promise.race([
                transport(settings.endpoint, {
                  method: "POST",
                  redirect: "error",
                  signal: controller.signal,
                  headers: {
                    Authorization: settings.auth,
                    "Content-Type": "application/json",
                  },
                  body: JSON.stringify(score),
                }),
                new Promise<never>((_, reject) => {
                  timer = setTimeout(
                    () => {
                      controller.abort();
                      reject(new ScoreOutboxError("DELIVERY_TIMEOUT"));
                    },
                    Math.min(
                      settings.timeout,
                      Math.max(1, deadline - Date.now()),
                    ),
                  );
                }),
              ]);
              // Do not read or retain response bodies, including error responses.
              if (response.body) void response.body.cancel().catch(() => {});
              if (!response.ok) {
                report.failedAttempts++;
                report.errorCodes.push("DELIVERY_HTTP");
                if (response.status !== 429 && response.status < 500) break;
                continue;
              }
              await locked(async () => {
                const current = await read();
                if (
                  !current.some((r) => r.type === "ack" && r.id === score.id)
                ) {
                  current.push({ type: "ack", id: score.id });
                  await write(current);
                }
              });
              report.delivered++;
              report.pending--;
              break;
            } catch (e) {
              if (
                e instanceof ScoreOutboxError &&
                !e.code.startsWith("DELIVERY_")
              )
                throw e;
              report.failedAttempts++;
              report.errorCodes.push(
                e instanceof ScoreOutboxError ? e.code : "DELIVERY_NETWORK",
              );
            } finally {
              if (timer) clearTimeout(timer);
            }
          }
        }
        const current = await locked(read);
        const ids = new Set(
          current
            .filter(
              (r): r is { type: "score"; score: Score } => r.type === "score",
            )
            .map((r) => r.score.id),
        );
        const acks = new Set(
          current
            .filter((r): r is { type: "ack"; id: string } => r.type === "ack")
            .map((r) => r.id),
        );
        report.queued = ids.size;
        report.pending = [...ids].filter((id) => !acks.has(id)).length;
        report.errorCodes = [...new Set(report.errorCodes)];
        return report;
      }
    },
  };
}
export function scoreConfigFromEnv(
  env: NodeJS.ProcessEnv = process.env,
): LangfuseScoreConfig {
  const config = {
    baseUrl: env.LANGFUSE_BASE_URL ?? "",
    publicKey: env.LANGFUSE_PUBLIC_KEY ?? "",
    secretKey: env.LANGFUSE_SECRET_KEY ?? "",
  };
  configValue(config);
  return config;
}
