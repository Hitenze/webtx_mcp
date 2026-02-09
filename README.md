# webtx-mcp

Lightweight MCP server that gives any Claude/Cursor agent access to Gemini with web search and reasoning. 5 tools, 4 dependencies, zero setup beyond an API key.

## Quick Start

```bash
cd /path/to/webtx_mcp
uv sync
uv run python -m webtx_mcp
```

## Add to Claude Code / Cursor

Add to your MCP config (e.g. `~/.claude/claude_desktop_config.json` or Cursor MCP settings):

```json
{
  "mcpServers": {
    "webtx": {
      "command": "/path/to/webtx_mcp/.venv/bin/python",
      "args": ["-m", "webtx_mcp"]
    }
  }
}
```

> **Warning:** Do NOT use `uv run python -m webtx_mcp` in your MCP config.
> This can conflict with conda/pyenv environments.
> Instead, use the absolute path to the venv Python:
> `/path/to/webtx_mcp/.venv/bin/python -m webtx_mcp`

## Add API Key

### Option 1: Interactive onboard (recommended for first-time setup)

```bash
uv run python -m webtx_mcp --onboard
```

Walks you through adding your Google API key with optional monthly limits.

### Option 2: Via MCP tool

Once the server is running, add your Google API key via the MCP tool:

```
api_add_key(key="AIza...", name="main")
```

### Option 3: Environment variable

Set `GOOGLE_API_KEY` in a `.env` file in the project root.

## Tools

### `ask_gemini`

Send a question to Gemini AI with web search and reasoning.

Execution mode:
- Blocking call (single request/response).
- For long prompts or heavy reasoning, this call can hit the MCP tool timeout in the host agent.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `question` | str | required | The question to ask |
| `model` | str | `"flash"` | `"flash"` or `"pro"` |
| `thinking` | str | `"medium"` | `"none"`, `"low"`, `"medium"`, `"high"` |
| `temperature` | float | `0.7` | Sampling temperature 0.0-1.0 |
| `google_search` | bool | `True` | Enable web search |

### `research_gemini`

Run Gemini research and save the final response text to a file.

Supports:
- `model="deep_research"` (default): uses Gemini Interactions API Deep Research agent
- `model="pro"` or `model="flash"`: uses standard generate_content flow

Execution mode:
- `deep_research` uses an asynchronous workflow internally (Gemini background interaction + polling).
- This tool still returns in one MCP call, so host-side MCP tool timeout still applies unless increased.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `question` | str | required | Research question |
| `output_path` | str | required | File path to write result |
| `model` | str | `"deep_research"` | `"deep_research"`, `"pro"`, or `"flash"` |
| `thinking` | str | `"medium"` | For `pro/flash`: `"none"`, `"low"`, `"medium"`, `"high"` |
| `temperature` | float | `0.7` | For `pro/flash`: sampling temperature |
| `google_search` | bool | `True` | For `pro/flash`: enable Google Search |
| `poll_interval_seconds` | int | `10` | For `deep_research`: polling interval |
| `timeout_seconds` | int | `1800` | For `deep_research`: max wait time |
| `thinking_summaries` | str | `"auto"` | For `deep_research`: `"auto"` or `"none"` |

### `api_add_key`

Add a Google API key. Supports multiple keys for load balancing.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `key` | str | required | Google API key |
| `name` | str | `""` | Display name |
| `monthly_limit` | int | `0` | Monthly limit (0=unlimited) |
| `daily_limit` | int | `0` | Daily limit (0=unlimited) |

### `api_list_keys`

List all keys with usage statistics.

### `api_remove_key`

Remove a key by ID.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `key_id` | int | required | Key ID from `api_list_keys` |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_API_KEY` | Fallback API key (used if no DB keys available) |
| `MCP_TRANSPORT` | `stdio` (default), `sse`, or `http` |
| `MCP_HOST` | Host for SSE/HTTP mode (default: `0.0.0.0`) |
| `MCP_PORT` | Port for SSE/HTTP mode (default: `8000`) |
| `MCP_PATH` | Path for HTTP mode (default: `/mcp`) |
| `WEBTX_MCP_DB_PATH` | Custom SQLite path (default: `~/.webtx_mcp/webtx.db`) |

## Key Management Features

- **Load balancing:** Multiple keys selected by lowest usage ratio
- **Auto-suspend:** 429 rate limit errors suspend key for 15 minutes
- **Auto-disable:** 401/403 auth errors disable key permanently
- **Circuit breaker:** 5 consecutive failures suspend key for 15 minutes
- **Encryption:** Keys encrypted at rest with Fernet
- **Env fallback:** Falls back to `GOOGLE_API_KEY` from `.env` if no DB keys

## License

MIT
