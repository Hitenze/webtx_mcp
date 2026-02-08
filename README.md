# webtx-mcp

Lightweight MCP server that gives any Claude/Cursor agent access to Gemini with web search and reasoning. 4 tools, 4 dependencies, zero setup beyond an API key.

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

Once the server is running, add your Google API key via the MCP tool:

```
api_add_key(key="AIza...", name="main")
```

Or set `GOOGLE_API_KEY` in a `.env` file in the project root.

## Tools

### `ask_gemini`

Send a question to Gemini AI with web search and reasoning.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `question` | str | required | The question to ask |
| `model` | str | `"flash"` | `"flash"` or `"pro"` |
| `thinking` | str | `"medium"` | `"none"`, `"low"`, `"medium"`, `"high"` |
| `temperature` | float | `0.7` | Sampling temperature 0.0-1.0 |
| `google_search` | bool | `True` | Enable web search |

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
