import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { createServer, type Server } from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

const packageDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const testRoot = fs.mkdtempSync(path.join(os.tmpdir(), "mem0-node-quick-loopback-"));
const unpackDir = path.join(testRoot, "unpacked");
const isolatedHome = path.join(testRoot, "home");
const cliPath = path.join(unpackDir, "package", "dist", "index.js");
interface FixtureMemory {
  id: string;
  user_id: string;
  memory: string;
}
let apiServer: Server;
let apiBaseUrl: string | undefined;
let responseForAdd: (memory: string) => { status: number; body: unknown };
let fixtureMemories: FixtureMemory[] = [];
let unscopedEnumerationRequests = 0;
let destructiveRequests = 0;

interface CliResult {
  stdout: string;
  stderr: string;
  exitCode: number;
}

function runCli(args: string[], timeoutMs = 15_000): Promise<CliResult> {
  const env: NodeJS.ProcessEnv = {
    PATH: process.env.PATH ?? "",
    TMPDIR: process.env.TMPDIR ?? os.tmpdir(),
    HOME: isolatedHome,
    XDG_CONFIG_HOME: path.join(testRoot, "xdg"),
    MEM0_API_KEY: "loopback-test-key",
    MEM0_BASE_URL: apiBaseUrl ?? "http://127.0.0.1:1",
    MEM0_TELEMETRY: "false",
    NO_COLOR: "1",
  };

  const child = spawn(process.execPath, [cliPath, ...args], {
    cwd: testRoot,
    env,
    signal: AbortSignal.timeout(timeoutMs),
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stdout.setEncoding("utf8").on("data", (chunk: string) => (stdout += chunk));
  child.stderr.setEncoding("utf8").on("data", (chunk: string) => (stderr += chunk));
  const completion = once(child, "close");
  return completion.then(([code, signal]) => {
    if (signal) throw new Error(`CLI exceeded ${timeoutMs}ms and exited via ${signal}`);
    return { stdout, stderr, exitCode: code ?? 1 };
  });
}

async function startApiFixture(): Promise<void> {
  fixtureMemories = [
    { id: "target-memory-id", user_id: "alice", memory: "protected target memory" },
    { id: "sentinel-memory-id", user_id: "bob", memory: "untouched sentinel memory" },
  ];
  unscopedEnumerationRequests = 0;
  destructiveRequests = 0;
  apiServer = createServer((request, response) => {
    const sendJson = (status: number, body: unknown) => {
      const json = JSON.stringify(body);
      response.writeHead(status, {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(json),
      });
      response.end(json);
    };
    const url = new URL(request.url ?? "/", apiBaseUrl ?? "http://127.0.0.1:1");

    if (request.method === "GET" && url.pathname === "/v1/ping/") {
      sendJson(200, { status: "ok" });
      return;
    }
    if (request.method === "GET" && url.pathname.startsWith("/v1/memories/")) {
      const memoryId = decodeURIComponent(url.pathname.split("/").at(-2) ?? "");
      const memory = fixtureMemories.find((item) => item.id === memoryId);
      sendJson(memory ? 200 : 404, memory ?? { detail: "not found" });
      return;
    }
    if (request.method === "DELETE") {
      destructiveRequests++;
      const memoryId = decodeURIComponent(url.pathname.split("/").at(-2) ?? "");
      const pathParts = url.pathname.split("/");
      const entityType = pathParts[3];
      const entityId = decodeURIComponent(pathParts[4] ?? "");
      if (url.pathname === "/v1/memories/") {
        fixtureMemories = [];
      } else if (entityType === "user" && pathParts[2] === "entities") {
        fixtureMemories = fixtureMemories.filter((item) => item.user_id !== entityId);
      } else {
        fixtureMemories = fixtureMemories.filter((item) => item.id !== memoryId);
      }
      sendJson(200, { deleted: true });
      return;
    }
    if (
      request.method !== "POST" ||
      (url.pathname !== "/v3/memories/add/" && url.pathname !== "/v3/memories/")
    ) {
      sendJson(404, { detail: "fixture route not found" });
      return;
    }

    let raw = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => (raw += chunk));
    request.on("end", () => {
      const payload = JSON.parse(raw) as {
        filters?: { user_id?: string };
        messages?: { content?: string }[];
      };
      if (url.pathname === "/v3/memories/") {
        if (!payload.filters || Object.keys(payload.filters).length === 0) {
          unscopedEnumerationRequests++;
          sendJson(400, { detail: "unscoped enumeration is forbidden" });
          return;
        }
        const matching = payload.filters.user_id
          ? fixtureMemories.filter((item) => item.user_id === payload.filters?.user_id)
          : [];
        const page = Number(url.searchParams.get("page") ?? 1);
        const pageSize = Number(url.searchParams.get("page_size") ?? 100);
        const start = (page - 1) * pageSize;
        sendJson(200, { results: matching.slice(start, start + pageSize) });
        return;
      }
      const result = responseForAdd(payload.messages?.[0]?.content ?? "");
      sendJson(result.status, result.body);
    });
  });
  const listening = once(apiServer, "listening");
  apiServer.listen(0, "127.0.0.1");
  await listening;
  const address = apiServer.address();
  if (!address || typeof address === "string") {
    throw new Error("loopback fixture did not bind to a TCP port");
  }
  apiBaseUrl = `http://127.0.0.1:${address.port}`;
}


function excerpt(text: string, maxLength = 1_200): string {
  return text.length > maxLength
    ? `${text.slice(0, maxLength)}… [${text.length - maxLength} chars omitted]`
    : text;
}

function parseJsonOutput(result: CliResult): Record<string, unknown> {
  try {
    return JSON.parse(result.stdout) as Record<string, unknown>;
  } catch (error) {
    throw new Error(
      `Expected exactly one JSON document on stdout; stdout=${JSON.stringify(excerpt(result.stdout))}, stderr=${JSON.stringify(result.stderr)}; ${String(error)}`,
    );
  }
}
async function runDeleteDryRun(args: string[]): Promise<Record<string, unknown>> {
  await startApiFixture();
  const result = await runCli(args);
  expect(result.exitCode, `stdout=${result.stdout}, stderr=${result.stderr}`).toBe(0);
  expect(destructiveRequests, "dry-run must not send a destructive API request").toBe(0);
  expect(fixtureMemories.map((memory) => memory.id)).toEqual([
    "target-memory-id",
    "sentinel-memory-id",
  ]);
  return parseJsonOutput(result);
}

beforeAll(async () => {
  fs.mkdirSync(unpackDir, { recursive: true });
  fs.mkdirSync(isolatedHome, { recursive: true });
  const build = spawnSync("pnpm", ["run", "build"], {
    cwd: packageDir,
    encoding: "utf8",
    timeout: 120_000,
  });
  if (build.error || build.status !== 0) {
    throw new Error(`pnpm run build failed: ${build.stderr || build.error?.message || build.status}`);
  }
  const pack = spawnSync("pnpm", ["pack", "--pack-destination", testRoot], {
    cwd: packageDir,
    encoding: "utf8",
    timeout: 60_000,
  });
  if (pack.error || pack.status !== 0) {
    throw new Error(`pnpm pack failed: ${pack.stderr || pack.error?.message || pack.status}`);
  }
  const tarball = fs.readdirSync(testRoot).find((name) => name.endsWith(".tgz"));
  if (!tarball) throw new Error("pnpm pack succeeded without producing a .tgz artifact");
  const extract = spawnSync("tar", ["-xzf", path.join(testRoot, tarball), "-C", unpackDir], {
    encoding: "utf8",
    timeout: 30_000,
  });
  if (extract.error || extract.status !== 0) {
    throw new Error(`tar extraction failed: ${extract.stderr || extract.error?.message || extract.status}`);
  }
  const dependencies = path.join(packageDir, "node_modules");
  if (!fs.existsSync(dependencies)) {
    throw new Error(`Node dependencies are required before this test: ${dependencies} is missing`);
  }
  fs.symlinkSync(dependencies, path.join(unpackDir, "package", "node_modules"), "dir");
  if (!fs.existsSync(cliPath)) throw new Error(`packed CLI entrypoint missing: ${cliPath}`);
}, 180_000);

afterEach(async () => {
  if (apiServer?.listening) {
    const closed = once(apiServer, "close");
    apiServer.close();
    await closed;
  }
});

afterAll(() => {
  fs.rmSync(testRoot, { recursive: true, force: true });
});

describe("quick offline regressions", () => {
  it.each([
    ["help --json", ["help", "--json"]],
    ["--json help", ["--json", "help"]],
    ["--agent help", ["--agent", "help"]],
  ])("C01 packed artifact %s returns nested command inventory", async (_label, args) => {
    const result = await runCli(args);
    expect(result.exitCode, `exit=${result.exitCode}, stderr=${result.stderr}`).toBe(0);
    const help = parseJsonOutput(result);
    expect(Array.isArray(help.commands), `stdout=${excerpt(result.stdout)}`).toBe(true);
    const search = (help.commands as Record<string, unknown>[]).find((command) => command.name === "search");
    expect(search, `packed help lacks search command: ${excerpt(result.stdout)}`).toBeDefined();
    const options = search?.options as Record<string, unknown>[];
    expect(options.some((option) => option.name === "top_k" && (option.flags as string[]).includes("--top-k"))).toBe(true);
  });

  it.each([
    ["global --json", ["--json", "import"]],
    ["--output json", ["import"]],
  ])("C09 all-failed %s import reports an error and nonzero exit", async (_label, command) => {
    await startApiFixture();
    responseForAdd = () => ({
      status: 503,
      body: { detail: "request rejected; credential loopback-test-key" },
    });
    const inputPath = path.join(testRoot, "all-failed.json");
    fs.writeFileSync(inputPath, JSON.stringify([{ memory: "known failing fixture row" }]));
    const args = command.includes("--json")
      ? [...command, inputPath]
      : [...command, inputPath, "--output", "json"];

    const result = await runCli(args);
    const envelope = parseJsonOutput(result);
    expect(result.exitCode, `stdout=${result.stdout}, stderr=${result.stderr}`).not.toBe(0);
    expect(envelope.status, `stdout=${result.stdout}`).toBe("error");
    expect(envelope.command).toBe("import");
    expect((envelope.data as Record<string, unknown>).added).toBe(0);
    expect((envelope.data as Record<string, unknown>).failed).toBe(1);
    expect(String(envelope.error)).toContain("HTTP 503");
    expect(String(envelope.error)).not.toContain("loopback-test-key");
  });
  it("C09 partial import preserves partial success while reporting exact added and failed counts", async () => {
    await startApiFixture();
    responseForAdd = (memory) =>
      memory === "fixture fails"
        ? { status: 503, body: { detail: "controlled fixture failure" } }
        : { status: 200, body: { id: "fixture-memory" } };
    const inputPath = path.join(testRoot, "partial.json");
    fs.writeFileSync(inputPath, JSON.stringify([{ memory: "fixture succeeds" }, { memory: "fixture fails" }]));

    const result = await runCli(["--json", "import", inputPath]);
    const envelope = parseJsonOutput(result);
    expect(result.exitCode, `stdout=${result.stdout}, stderr=${result.stderr}`).toBe(0);
    expect(envelope.status).toBe("success");
    expect((envelope.data as Record<string, unknown>).added).toBe(1);
    expect((envelope.data as Record<string, unknown>).failed).toBe(1);
  });

  it.each([
    ["C09 global --json", ["--json", "import"]],
    ["C09 --output json", ["import"]],
  ])("%s successful multi-row import writes one JSON document only", async (_label, command) => {
    await startApiFixture();
    const inputPath = path.join(testRoot, "successful.json");
    const memories = [{ memory: "fixture success one" }, { text: "fixture success two" }];
    fs.writeFileSync(inputPath, JSON.stringify(memories));
    const args = command.includes("--json")
      ? [...command, inputPath]
      : [...command, inputPath, "--output", "json"];

    const result = await runCli(args);
    const envelope = parseJsonOutput(result);
    expect(result.exitCode, `stdout=${result.stdout}, stderr=${result.stderr}`).toBe(0);
    expect(envelope.status).toBe("success");
    expect((envelope.data as Record<string, unknown>).added).toBe(2);
    expect((envelope.data as Record<string, unknown>).failed).toBe(0);
  });

  it("C10 single-ID dry-run returns the protected memory in a JSON preview", async () => {
    const envelope = await runDeleteDryRun([
      "--json",
      "delete",
      "target-memory-id",
      "--dry-run",
      "--force",
    ]);
    const preview = envelope.data as Record<string, unknown>;
    expect(envelope.status).toBe("success");
    expect(envelope.command).toBe("delete");
    expect(preview.dry_run).toBe(true);
    expect(preview.id).toBe("target-memory-id");
    expect(preview.memory).toBe("protected target memory");
  });

  it("C10 scoped all-memory dry-run returns its matching count and scope", async () => {
    const envelope = await runDeleteDryRun([
      "--json",
      "delete",
      "--all",
      "--user-id",
      "alice",
      "--dry-run",
      "--force",
    ]);
    const preview = envelope.data as Record<string, unknown>;
    expect(envelope.status).toBe("success");
    expect(envelope.command).toBe("delete-all");
    expect(preview.dry_run).toBe(true);
    expect(envelope.count).toBe(1);
    expect((envelope.scope as Record<string, unknown>).user_id).toBe("alice");
  });

  it.each([100, 137])("C10 scoped dry-run counts all %i matching rows across pages", async (rowCount) => {
    await startApiFixture();
    fixtureMemories.push(...Array.from({ length: rowCount - 1 }, (_, i) => ({
      id: `paged-${i}`,
      user_id: "alice",
      memory: `Protected memory ${i}`,
    })));
    const protectedIds = fixtureMemories.map((memory) => memory.id);
    const result = await runCli([
      "--json", "delete", "--all", "--user-id", "alice", "--dry-run", "--force",
    ]);
    expect(result.exitCode, `stdout=${result.stdout}, stderr=${result.stderr}`).toBe(0);
    const envelope = parseJsonOutput(result);
    expect(envelope.count).toBe(rowCount);
    expect(envelope.scope).toEqual({ user_id: "alice" });
    expect((envelope.data as Record<string, unknown>).dry_run).toBe(true);
    expect(destructiveRequests).toBe(0);
    expect(fixtureMemories.map((memory) => memory.id)).toEqual(protectedIds);
  });

  it("C10 project-wide dry-run previews action and scope without reading or deleting", async () => {
    const envelope = await runDeleteDryRun([
      "--json",
      "delete",
      "--all",
      "--project",
      "--dry-run",
      "--force",
    ]);
    const preview = envelope.data as Record<string, unknown>;
    expect(envelope.status).toBe("success");
    expect(envelope.command).toBe("delete-all");
    expect(preview.dry_run).toBe(true);
    expect(envelope.scope).toEqual({ project: "true" });
    expect(envelope).not.toHaveProperty("count");
    expect(unscopedEnumerationRequests).toBe(0);
  });

  it("C10 project-wide text dry-run reports action and makes no changes", async () => {
    await startApiFixture();
    const result = await runCli([
      "delete",
      "--all",
      "--project",
      "--dry-run",
      "--force",
      "--output",
      "text",
    ]);
    expect(result.exitCode, `stderr=${result.stderr}`).toBe(0);
    expect(result.stderr).toContain("Would delete all memories across the project.");
    expect(result.stderr).toContain("No changes made.");
    expect(unscopedEnumerationRequests).toBe(0);
    expect(destructiveRequests).toBe(0);
    expect(fixtureMemories.map((memory) => memory.id)).toEqual([
      "target-memory-id",
      "sentinel-memory-id",
    ]);
  });

  it("C10 entity dry-run returns the target scope without cascading deletion", async () => {
    const envelope = await runDeleteDryRun([
      "--json",
      "delete",
      "--entity",
      "--user-id",
      "alice",
      "--dry-run",
      "--force",
    ]);
    const preview = envelope.data as Record<string, unknown>;
    expect(envelope.status).toBe("success");
    expect(envelope.command).toBe("entity delete");
    expect(preview.dry_run).toBe(true);
    expect((envelope.scope as Record<string, unknown>).user_id).toBe("alice");
  });

});
