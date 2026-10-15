import { execFileSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { delimiter, join } from "node:path";
import { fileURLToPath } from "node:url";

const hookPath = fileURLToPath(new URL("./hook.py", import.meta.url));
const minimumPython = [3, 10];
const toolNames = {
  read: "Read", write: "Write", edit: "Edit", apply_patch: "Edit",
  bash: "Bash", grep: "Grep", glob: "Glob", web_search: "WebSearch",
};

function pythonVersion(executable) {
  try {
    const output = execFileSync(executable, ["-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      timeout: 2000,
    }).trim();
    const [major, minor] = output.split(".").map(Number);
    return Number.isInteger(major) && Number.isInteger(minor) &&
      (major > minimumPython[0] || (major === minimumPython[0] && minor >= minimumPython[1]));
  } catch {
    return false;
  }
}

function pythonFor(ctx) {
  const override = process.env.MEM0_PLUGIN_PYTHON;
  if (override) {
    if (!pythonVersion(override)) throw new Error("MEM0_PLUGIN_PYTHON must name Python 3.10 or newer");
    return override;
  }

  const local = join(ctx.cwd, ".venv", "bin", "python");
  if (existsSync(local) && pythonVersion(local)) return local;

  for (const directory of (process.env.PATH ?? "").split(delimiter)) {
    for (const name of ["python3", "python"]) {
      const candidate = join(directory, name);
      if (existsSync(candidate) && pythonVersion(candidate)) return candidate;
    }
  }
  throw new Error("No Python 3.10+ interpreter found; set MEM0_PLUGIN_PYTHON");
}

function sessionId(ctx, pi) {
  try {
    const id = ctx.sessionManager.getSessionId();
    if (typeof id === "string" && id.length > 0) return id;
  } catch {
    // The host may not have initialized a session manager yet.
  }
  pi.logger?.warn?.("Mem0 OMP adapter skipped an event without a session ID");
  return undefined;
}

function assistantText(event) {
  const messages = Array.isArray(event?.messages) ? event.messages : [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "assistant") continue;
    if (typeof message.content === "string") return message.content;
    if (!Array.isArray(message.content)) continue;
    return message.content
      .filter((part) => part?.type === "text" && typeof part.text === "string")
      .map((part) => part.text)
      .join("");
  }
  return "";
}

function runHook(pi, ctx, state, action, input, reason) {
  state.queue = state.queue.then(() => new Promise((resolve) => {
    let executable;
    try {
      executable = state.executable ??= pythonFor(ctx);
    } catch (error) {
      pi.logger?.error?.(`Mem0 OMP ${action}: ${error.message}`);
      resolve("");
      return;
    }

    const env = { ...process.env };
    const dataDir = process.env.MEM0_CODE_DATA_DIR || process.env.MEM0_PLUGIN_DATA_DIR || process.env.CLAUDE_PLUGIN_DATA;
    if (dataDir) env.MEM0_CODE_DATA_DIR = dataDir;
    const child = spawn(executable, [hookPath, action, "--reason", reason], {
      cwd: ctx.cwd,
      env,
      stdio: ["pipe", "pipe", "ignore"],
    });
    let stdout = "";
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.on("error", () => {
      pi.logger?.error?.(`Mem0 OMP ${action}: could not start Python hook`);
      resolve("");
    });
    child.on("close", (code) => {
      if (code !== 0) {
        pi.logger?.error?.(`Mem0 OMP ${action}: Python hook exited with status ${code}`);
        resolve("");
        return;
      }
      resolve(stdout.trim());
    });
    child.stdin.on("error", () => {
      pi.logger?.error?.(`Mem0 OMP ${action}: could not deliver hook input`);
    });
    child.stdin.end(JSON.stringify({ ...input, session_id: state.sessionId, cwd: ctx.cwd }));
  })).catch((error) => {
    pi.logger?.error?.(`Mem0 OMP ${action}: event delivery failed (${error?.name ?? "Error"})`);
    return "";
  });
  return state.queue;
}

export default function mem0OmpExtension(pi) {
  const sessions = new Map();
  function stateFor(ctx) {
    const id = sessionId(ctx, pi);
    if (!id) return undefined;
    let state = sessions.get(id);
    if (!state) {
      state = { sessionId: id, queue: Promise.resolve() };
      sessions.set(id, state);
    }
    return state;
  }

  pi.on("session_start", async (event, ctx) => {
    const state = stateFor(ctx);
    if (!state) return;
    await runHook(pi, ctx, state, "session-start", { source: "startup" }, "session-start");
  });

  pi.on("before_agent_start", async (event, ctx) => {
    const state = stateFor(ctx);
    if (!state || typeof event?.prompt !== "string") return;
    const output = await runHook(pi, ctx, state, "user-prompt", { prompt: event.prompt }, "prompt");
    if (!output) return;
    try {
      const parsed = JSON.parse(output);
      const context = parsed?.hookSpecificOutput?.additionalContext;
      if (typeof context === "string" && context.length > 0) {
        return { message: { customType: "mem0-context", content: context, display: false, attribution: "user" } };
      }
    } catch {
      pi.logger?.warn?.("Mem0 OMP adapter ignored invalid recall output");
    }
  });

  pi.on("tool_result", async (event, ctx) => {
    const state = stateFor(ctx);
    if (!state) return;
    const content = Array.isArray(event?.content)
      ? event.content.filter((part) => part?.type === "text").map((part) => part.text).join("\n")
      : event?.content;
    const name = toolNames[event?.toolName] ?? event?.toolName;
    const input = event?.input ?? {};
    const fileTool = name === "Read" || name === "Write" || name === "Edit";
    const paths = fileTool && Array.isArray(input.paths) ? input.paths : [input.path];
    for (const path of paths.length ? paths : [undefined]) {
      const toolInput = { ...input };
      if (fileTool && typeof path === "string") toolInput.file_path = path;
      if (name === "Edit" && paths.length === 1 && typeof event?.details?.newText === "string") {
        toolInput.new_string = event.details.newText;
      }
      await runHook(pi, ctx, state, event?.isError ? "post-tool-failure" : "post-tool", {
        tool_name: name,
        tool_input: toolInput,
        tool_response: content,
        error: event?.isError ? content : "",
      }, "tool-result");
    }
  });

  pi.on("agent_end", async (event, ctx) => {
    const state = stateFor(ctx);
    if (!state) return;
    const message = assistantText(event).trim();
    if (!message) return;
    await runHook(pi, ctx, state, "stop", { last_assistant_message: message }, "agent-end");
  });

  pi.on("session_before_compact", async (_event, ctx) => {
    const state = stateFor(ctx);
    if (!state) return;
    await runHook(pi, ctx, state, "flush", {}, "pre-compact");
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    const state = stateFor(ctx);
    if (!state) return;
    await runHook(pi, ctx, state, "flush", {}, "session-end");
    sessions.delete(state.sessionId);
  });
}
