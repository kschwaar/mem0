# Plan: Install & Test Local mem0 Plugin with Claude Code

## Prerequisites

Complete `MCP_PLAN.md` first. The MCP server must be running at
`http://localhost:8888/mcp/` before this plan makes sense.

---

## Goal

Install the locally patched `integrations/mem0-plugin` into Claude Code,
pointed at your local `server/` MCP endpoint, with lifecycle hooks also
redirected to localhost so auto-capture and session summaries work end-to-end.

---

## Step 1: Patch `.mcp.json`

File: `integrations/mem0-plugin/.mcp.json`

Replace entirely with:

```json
{
  "mcpServers": {
    "mem0": {
      "type": "http",
      "url": "http://localhost:8888/mcp/",
      "headers": {
        "X-API-Key": "${MEM0_API_KEY}",
        "X-Agent-ID": "claude-code",
        "X-User-ID": "${MEM0_USER_ID}"
      }
    }
  }
}
```

> `MEM0_API_KEY` is your existing server API key (the `m0sk_...` key from
> your setup). `MEM0_USER_ID` is your identity string (e.g. `"kevin"`).
> Both must be exported in your shell environment.

---

## Step 2: Patch the lifecycle hook scripts

8 files hard-code `https://api.mem0.ai`. Each needs to respect a
`MEM0_BASE_URL` env var with that URL as the fallback.

### Python scripts (7 files)

Each has a line near the top like:
```python
API_URL = "https://api.mem0.ai"
```

Replace with:
```python
import os
API_URL = os.environ.get("MEM0_BASE_URL", "https://api.mem0.ai").rstrip("/")
```

Files to patch:
- `scripts/auto_import.py` (line 44)
- `scripts/capture_session_summary.py` (line 45)
- `scripts/_search.py` (line 13 — variable is `SEARCH_URL`, set it to
  `f"{API_URL}/v3/memories/search/"` after setting `API_URL`)
- `scripts/session_timeline.py` (line 24)
- `scripts/on_pre_compact.py` (line 45)
- `scripts/capture_compact_summary.py` (line 47)
- `scripts/auto_capture.py` (line 42)
- `scripts/import_competing_tools.py` (line 36)

### Shell script (1 file)

File: `scripts/on_session_start.sh` (line 84) — contains an inline Python
block that calls `https://api.mem0.ai/v3/memories/...`. Find the python block
and add env var resolution before the URL:

```bash
# Find this pattern inside the shell script:
'https://api.mem0.ai/v3/memories/?page=1&page_size=1',

# Replace with:
os.environ.get('MEM0_BASE_URL', 'https://api.mem0.ai').rstrip('/') + '/v3/memories/?page=1&page_size=1',
```

> **Note:** The hooks call v3 endpoints (`/v3/memories/search/`, etc.). These
> don't exist on your local server yet — that's Plan 3. Until Plan 3 is done,
> the hooks will fail gracefully (they all have `|| true` error suppression) but
> the MCP tools will still work. You can complete Plan 3 before or after this
> step.

---

## Step 3: Set env vars

Verify the following are in the shell profile and are exported
MEM0_API_KEY, MEM0_USER_ID, MEM0_BASE_URL

> **Warning:** Do not also enter an API key into Claude Code's interactive
> plugin config UI for `mem0`. That value is injected as
> `CLAUDE_PLUGIN_OPTION_API_KEY`, which the hook scripts' `resolve_api_key()`
> (`scripts/_identity.py`/`_identity.sh`) prefer over `MEM0_API_KEY` only if
> the latter is unset — but `.mcp.json`'s `"${MEM0_API_KEY}"` interpolation
> never sees `CLAUDE_PLUGIN_OPTION_API_KEY` at all, only the real shell env
> var. Mixing the two creates a split-brain where hooks and the MCP
> connection silently authenticate with different keys. As long as
> `MEM0_API_KEY` stays exported and the config UI field is left blank, both
> paths agree.

---

## Step 4: Patch `plugin.json` — remove the required api_key userConfig

File: `integrations/mem0-plugin/.claude-plugin/plugin.json`

The `userConfig.api_key` field currently marks the key as `"required": true`
and points users at `app.mem0.ai`. Since you're using env vars instead of the
plugin's config UI, change `required` to `false` and update the description:

```json
{
  "name": "mem0",
  "version": "0.2.11-local",
  "description": "Persistent memory for Claude Code — local server build.",
  "author": { "name": "Mem0", "email": "support@mem0.ai" },
  "homepage": "https://mem0.ai",
  "repository": "https://github.com/mem0ai/mem0",
  "license": "Apache-2.0",
  "keywords": ["memory", "personalization", "mcp", "semantic-search"],
  "userConfig": {
    "api_key": {
      "type": "string",
      "title": "Mem0 API Key",
      "description": "Set MEM0_API_KEY in your shell environment instead.",
      "sensitive": true,
      "required": false
    }
  }
}
```

---

## Step 5: Uninstall any existing mem0 plugin

Check if the marketplace version is installed:
```bash
claude plugin list
```

If `mem0` appears, uninstall it:
```bash
claude plugin uninstall mem0
```

---

## Step 6: Install the local plugin

```bash
claude plugin install /Users/kschwaar/sandbox/mem0/integrations/mem0-plugin
```

Claude Code reads `integrations/mem0-plugin/.claude-plugin/plugin.json` to
discover the plugin. The `${extensionPath}` variable in `hooks.json` resolves
to the install path, so all hook scripts run from your local source directory.

---

## Step 7: Verify

### 7a. Check MCP tools are registered

Start a new Claude Code session. Run:
```
/mcp
```
You should see `mem0` listed with all 9 tools per `MCP_PLAN.md`:
`add_memory`, `search_memories`, `get_memories`, `get_memory`,
`update_memory`, `delete_memory`, `delete_all_memories`, `delete_entities`,
`list_entities`.

### 7b. Test add and search

Ask Claude Code:
```
Remember that I prefer TypeScript over JavaScript for new projects.
```
Claude should call `add_memory`. Then ask:
```
What are my project preferences?
```
Claude should call `search_memories` and return the stored preference.

### 7c. Verify in the dashboard

Open `http://localhost:3333` — the memory should appear with:
- Content: the preference text
- User: `kevin`
- Agent: `claude-code`

### 7d. Check hook logs

Hook output (including errors) goes to `~/.mem0/hooks.log`. After a session
start and stop:
```bash
tail -50 ~/.mem0/hooks.log
```
Errors about v3 endpoints (search, add via hooks) are expected until Plan 3
is complete. MCP tool errors are not expected.

### 7e. Confirm the Step 2 hook patches actually took

Nothing above exercises the `MEM0_BASE_URL` patch directly — 7d only shows
that hooks fail, not *where* they're pointed. Before assuming Step 2 worked,
confirm all 8 files were patched and none still reference the hardcoded
fallback in a way that skips the env var:

```bash
grep -L "MEM0_BASE_URL" integrations/mem0-plugin/scripts/auto_import.py \
  integrations/mem0-plugin/scripts/capture_session_summary.py \
  integrations/mem0-plugin/scripts/_search.py \
  integrations/mem0-plugin/scripts/session_timeline.py \
  integrations/mem0-plugin/scripts/on_pre_compact.py \
  integrations/mem0-plugin/scripts/capture_compact_summary.py \
  integrations/mem0-plugin/scripts/auto_capture.py \
  integrations/mem0-plugin/scripts/import_competing_tools.py \
  integrations/mem0-plugin/scripts/on_session_start.sh
```
This should print nothing (empty output = all 8 files contain the string).
Then check `~/.mem0/hooks.log` for a line showing a request to
`localhost:8888` rather than `api.mem0.ai` — confirming the patched URL is
actually being hit (even if the request then 404s pending Plan 3).

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| MCP tools don't appear in `/mcp` | Plugin not installed, or MCP server not running |
| `add_memory` returns auth error | `MEM0_API_KEY` env var not set or wrong key |
| `add_memory` returns "user_id not set" | `MEM0_USER_ID` not set, or `X-User-ID` header not reaching MCP server |
| Hooks fail with connection errors | `MEM0_BASE_URL` not set, or server not running |
| Memory not in dashboard | Check Qdrant directly: `curl http://localhost:6333/collections/memories/points/scroll -H 'Content-Type: application/json' -d '{"limit":10,"with_payload":true,"with_vector":false}'` |
