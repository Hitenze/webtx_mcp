"""
FastMCP server for webtx-mcp.
Provides 7 tools: ask_gemini, research_gemini_start, research_gemini_status,
research_gemini_cancel, api_add_key, api_list_keys, api_remove_key.
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP

# Load .env from project root
project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

# Configure logging to stderr to avoid stdout pollution
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger(__name__)

from .gemini_client import (
    GEMINI_FLASH,
    GEMINI_PRO,
    cancel_gemini_interaction,
    extract_interaction_text,
    get_gemini_interaction,
    query_gemini,
    start_gemini_deep_research,
)
from .key_manager import get_key_manager
from .research_jobs import (
    cleanup_old_jobs,
    create_job,
    get_job,
    mark_saved,
    set_error,
    set_error_only,
    update_status,
)

# Create the FastMCP server instance
mcp = FastMCP("webtx-mcp")


# ============================================================
# Tools
# ============================================================


def _resolve_output_path(output_path: str) -> Path:
    """
    Resolve output path for research files.

    Relative paths are resolved under project root.
    """
    path = Path(output_path).expanduser()
    if not path.is_absolute():
        path = (project_root / path).resolve()
    else:
        path = path.resolve()
    return path


def _json_result(action: str, ok: bool, **kwargs) -> str:
    """Build a stable JSON string result for MCP tools."""
    payload = {"ok": ok, "action": action}
    payload.update(kwargs)
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool(
    description=(
        "Blocking Gemini call with web search and reasoning. "
        "May time out for long-running prompts under host MCP tool timeout limits."
    )
)
async def ask_gemini(
    question: str,
    model: str = "flash",
    thinking: str = "medium",
    temperature: float = 0.7,
    google_search: bool = True,
) -> str:
    """
    Send a question to Gemini AI with configurable parameters.

    Execution mode:
    - Blocking (single request/response).
    - May exceed host-side MCP tool timeout for long-running requests.

    Args:
        question: The question or prompt to send to Gemini
        model: Model to use - "pro" for gemini-3.1-pro-preview
               (fallback: 3-pro → flash), "flash" for gemini-3-flash-preview
               (default: "flash")
        thinking: Thinking level - "none", "low", "medium", "high"
                  (default: "medium")
        temperature: Sampling temperature 0.0-1.0 (default: 0.7)
        google_search: Enable Google Search for up-to-date information
                       (default: True)

    Returns:
        Gemini's response text
    """
    try:
        # Input validation
        if not question or not question.strip():
            return "Error: Question cannot be empty"

        if len(question) > 10000:
            return "Error: Question too long (max 10000 characters)"

        model_key = (model or "").strip().lower()
        if model_key not in ("flash", "pro"):
            return "Error: Invalid model. Use one of: flash, pro"

        logger.info(f"[Gemini] Processing question: {question[:100]}...")

        # Map model string
        gemini_model = GEMINI_PRO if model_key == "pro" else GEMINI_FLASH

        # Map thinking string
        thinking_level = thinking.upper()
        if thinking_level not in ("NONE", "LOW", "MEDIUM", "HIGH"):
            thinking_level = "MEDIUM"

        # Query Gemini
        response = await query_gemini(
            question=question,
            model=gemini_model,
            thinking_level=thinking_level,
            temperature=temperature,
            google_search=google_search,
        )

        if isinstance(response, str) and response.startswith("Error:"):
            logger.error(f"[Gemini] Error: {response}")
            return response

        logger.info(f"[Gemini] Successfully received response ({len(response)} chars)")
        return response

    except Exception as e:
        logger.exception(f"[Gemini] Unexpected error: {e}")
        return f"Error: {str(e)}"


@mcp.tool(
    description=(
        "Start a Gemini Deep Research background interaction and persist task metadata."
    )
)
async def research_gemini_start(
    question: str,
    output_path: str,
    thinking_summaries: str = "auto",
) -> str:
    """
    Start a Deep Research interaction and store job metadata in SQLite.

    Args:
        question: Deep research question
        output_path: File path to write result on completion
        thinking_summaries: "auto" or "none"

    Returns:
        JSON string with task metadata and interaction id
    """
    try:
        cleanup_old_jobs(days=30)

        if not question or not question.strip():
            return _json_result(
                "start",
                False,
                error="Question cannot be empty",
            )

        if len(question) > 10000:
            return _json_result(
                "start",
                False,
                error="Question too long (max 10000 characters)",
            )

        if not output_path or not output_path.strip():
            return _json_result(
                "start",
                False,
                error="output_path cannot be empty",
            )

        resolved_output = str(_resolve_output_path(output_path))
        logger.info(f"[ResearchStart] Processing question: {question[:100]}...")

        result = await start_gemini_deep_research(
            question=question,
            thinking_summaries=thinking_summaries,
        )
        if isinstance(result, str) and result.startswith("Error:"):
            logger.error(f"[ResearchStart] Error: {result}")
            return _json_result(
                "start",
                False,
                error=result,
                status="failed",
                output_path=resolved_output,
            )

        interaction_id, status = result
        create_job(
            interaction_id=interaction_id,
            question=question,
            output_path=resolved_output,
            model="deep_research",
            status=status,
        )

        logger.info(
            "[ResearchStart] Started interaction %s status=%s output_path=%s",
            interaction_id,
            status,
            resolved_output,
        )
        return _json_result(
            "start",
            True,
            interaction_id=interaction_id,
            status=status,
            model="deep_research",
            output_path=resolved_output,
        )

    except Exception as e:
        logger.exception(f"[ResearchStart] Unexpected error: {e}")
        return _json_result(
            "start",
            False,
            error=str(e),
            status="failed",
        )


@mcp.tool(
    description=(
        "Check status of a Gemini Deep Research interaction once. "
        "When completed, writes output to the stored output_path."
    )
)
async def research_gemini_status(interaction_id: str) -> str:
    """
    Check Deep Research interaction status and write output when completed.

    Args:
        interaction_id: Interaction id from research_gemini_start

    Returns:
        JSON string with current status and file save state
    """
    try:
        cleanup_old_jobs(days=30)

        if not interaction_id or not interaction_id.strip():
            return _json_result(
                "status",
                False,
                error="interaction_id cannot be empty",
                status="invalid",
            )

        interaction_id = interaction_id.strip()
        job = get_job(interaction_id)
        if not job:
            return _json_result(
                "status",
                False,
                interaction_id=interaction_id,
                status="not_found",
                error="Job not found for interaction_id",
            )

        output_path = job["output_path"]
        output_chars = int(job.get("output_chars") or 0)
        saved = bool(job.get("saved_at"))

        result = await get_gemini_interaction(interaction_id)
        if isinstance(result, str) and result.startswith("Error:"):
            set_error(interaction_id, result, status="failed")
            return _json_result(
                "status",
                False,
                interaction_id=interaction_id,
                status="failed",
                output_path=output_path,
                saved=saved,
                output_chars=output_chars,
                error=result,
            )

        interaction = result
        status = getattr(interaction, "status", "unknown")
        update_status(interaction_id, status)

        if status == "completed" and not saved:
            try:
                output = extract_interaction_text(interaction)
                if not output:
                    raise ValueError(
                        "Deep Research completed but returned empty output."
                    )
                path = Path(output_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(output, encoding="utf-8")
                output_chars = len(output)
                saved = True
                mark_saved(interaction_id, output_chars, status="completed")
            except Exception as e:
                error = str(e)
                set_error(interaction_id, error, status="failed")
                return _json_result(
                    "status",
                    False,
                    interaction_id=interaction_id,
                    status="failed",
                    output_path=output_path,
                    saved=False,
                    output_chars=output_chars,
                    error=error,
                )

        if status == "failed":
            details = "Gemini interaction failed."
            if hasattr(interaction, "model_dump"):
                payload = interaction.model_dump(exclude_none=True)
                if isinstance(payload, dict) and payload.get("error"):
                    details = f"Gemini interaction failed: {payload['error']}"
            set_error(interaction_id, details, status="failed")
            return _json_result(
                "status",
                False,
                interaction_id=interaction_id,
                status="failed",
                output_path=output_path,
                saved=saved,
                output_chars=output_chars,
                error=details,
            )

        # Refresh metadata for a stable response payload.
        latest = get_job(interaction_id) or {}
        return _json_result(
            "status",
            True,
            interaction_id=interaction_id,
            status=latest.get("status", status),
            output_path=latest.get("output_path", output_path),
            saved=bool(latest.get("saved_at")),
            output_chars=int(latest.get("output_chars") or output_chars),
        )

    except Exception as e:
        logger.exception(f"[ResearchStatus] Unexpected error: {e}")
        return _json_result(
            "status",
            False,
            interaction_id=interaction_id,
            status="failed",
            error=str(e),
        )


@mcp.tool(
    description="Cancel a Gemini Deep Research interaction and update local task state."
)
async def research_gemini_cancel(interaction_id: str) -> str:
    """
    Cancel Deep Research interaction.

    Args:
        interaction_id: Interaction id from research_gemini_start

    Returns:
        JSON string with cancellation result
    """
    try:
        if not interaction_id or not interaction_id.strip():
            return _json_result(
                "cancel",
                False,
                error="interaction_id cannot be empty",
                status="invalid",
            )

        interaction_id = interaction_id.strip()
        job = get_job(interaction_id)
        if not job:
            return _json_result(
                "cancel",
                False,
                interaction_id=interaction_id,
                status="not_found",
                error="Job not found for interaction_id",
            )

        result = await cancel_gemini_interaction(interaction_id)
        if isinstance(result, str) and result.startswith("Error:"):
            # If cancel fails, verify current remote state before mutating local status.
            remote = await get_gemini_interaction(interaction_id)
            if not (isinstance(remote, str) and remote.startswith("Error:")):
                remote_status = getattr(remote, "status", "unknown")
                if remote_status in ("completed", "cancelled"):
                    update_status(interaction_id, remote_status)
                    latest = get_job(interaction_id) or job
                    return _json_result(
                        "cancel",
                        True,
                        interaction_id=interaction_id,
                        status=latest.get("status", remote_status),
                        output_path=latest.get("output_path"),
                        saved=bool(latest.get("saved_at")),
                        output_chars=int(latest.get("output_chars") or 0),
                        warning=(
                            "Interaction already in terminal state; "
                            "cancel had no effect."
                        ),
                    )

            # Keep status unchanged when cancellation cannot be confirmed.
            set_error_only(interaction_id, result)
            latest = get_job(interaction_id) or job
            return _json_result(
                "cancel",
                False,
                interaction_id=interaction_id,
                status=latest.get("status", job.get("status", "unknown")),
                output_path=latest.get("output_path", job.get("output_path")),
                saved=bool(latest.get("saved_at")),
                output_chars=int(latest.get("output_chars") or 0),
                error=result,
            )

        status, _ = result
        final_status = status or "cancelled"
        update_status(interaction_id, final_status)

        latest = get_job(interaction_id) or job
        return _json_result(
            "cancel",
            True,
            interaction_id=interaction_id,
            status=latest.get("status", final_status),
            output_path=latest.get("output_path"),
            saved=bool(latest.get("saved_at")),
            output_chars=int(latest.get("output_chars") or 0),
        )

    except Exception as e:
        logger.exception(f"[ResearchCancel] Unexpected error: {e}")
        return _json_result(
            "cancel",
            False,
            interaction_id=interaction_id,
            status="failed",
            error=str(e),
        )


@mcp.tool(description="Add a Google API key for Gemini access")
async def api_add_key(
    key: str,
    name: str = "",
    monthly_limit: int = 0,
    daily_limit: int = 0,
) -> str:
    """
    Add a new Google API key.

    Supports multiple keys for load balancing.
    Keys are selected based on usage ratio (lowest first).

    Args:
        key: The Google API key value
        name: Optional display name for identification
        monthly_limit: Monthly usage limit (0 = unlimited)
        daily_limit: Daily usage limit (0 = unlimited)

    Returns:
        Success message with key ID or error message
    """
    logger.info("[KeyManager] Adding key for google")
    manager = get_key_manager()
    result = manager.add_key(
        key=key,
        name=name,
        monthly_limit=monthly_limit,
        daily_limit=daily_limit,
    )

    if result.get("success"):
        return (
            f"Added API key {result['key_id']} for google"
            + (f" ({name})" if name else "")
            + (f" with monthly limit {monthly_limit}" if monthly_limit > 0 else "")
        )
    else:
        return f"Error: {result.get('error', 'Unknown error')}"


@mcp.tool(description="List Google API keys with usage statistics")
async def api_list_keys() -> str:
    """
    List all Google API keys with usage statistics.

    Shows key metadata and usage info (not the actual key values).

    Returns:
        Formatted list of keys with usage stats
    """
    logger.info("[KeyManager] Listing keys")
    manager = get_key_manager()
    keys = manager.list_keys()

    if not keys:
        return "No API keys found"

    lines = [f"API Keys ({len(keys)} total):\n"]
    for k in keys:
        status_icon = (
            "ok"
            if k["status"] == "active"
            else ("paused" if k["status"] == "suspended" else "off")
        )
        usage_info = (
            f"{k['usage_count']}/{k['monthly_limit']}"
            if k["monthly_limit"] > 0
            else str(k["usage_count"])
        )
        lines.append(
            f"  [{status_icon}] [{k['id']}] google: {k['name'] or '(unnamed)'}"
        )
        lines.append(f"      Usage: {usage_info} (ratio: {k['usage_ratio']:.2%})")
        if k["status"] == "suspended" and k["suspended_until"]:
            lines.append(f"      Suspended until: {k['suspended_until']}")
        if k["consecutive_failures"] > 0:
            lines.append(f"      Consecutive failures: {k['consecutive_failures']}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool(description="Remove a Google API key by ID")
async def api_remove_key(key_id: int) -> str:
    """
    Remove a Google API key from the system.

    Args:
        key_id: ID of the key to remove (from api_list_keys)

    Returns:
        Success or error message
    """
    logger.info(f"[KeyManager] Removing key {key_id}")
    manager = get_key_manager()
    result = manager.remove_key(key_id)

    if result.get("success"):
        return f"Removed API key {key_id}" + (
            f" ({result.get('name')})" if result.get("name") else ""
        )
    else:
        return f"Error: {result.get('error', 'Unknown error')}"


# ============================================================
# Server entry point
# ============================================================


def main() -> None:
    """Main entry point for the MCP server."""
    logger.info(
        "Starting webtx-mcp server with 7 tools: ask_gemini, "
        "research_gemini_start, research_gemini_status, research_gemini_cancel, "
        "api_add_key, api_list_keys, api_remove_key"
    )

    transport_mode = os.getenv("MCP_TRANSPORT", "stdio").lower()

    try:
        if transport_mode == "sse":
            host = os.getenv("MCP_HOST", "0.0.0.0")
            port = int(os.getenv("MCP_PORT", "8000"))
            logger.info(f"Running in SSE mode at {host}:{port}")
            mcp.run(transport="sse", host=host, port=port)
        elif transport_mode == "http":
            host = os.getenv("MCP_HOST", "0.0.0.0")
            port = int(os.getenv("MCP_PORT", "8000"))
            path = os.getenv("MCP_PATH", "/mcp")
            logger.info(f"Running in HTTP mode at {host}:{port}{path}")
            mcp.run(transport="http", host=host, port=port, path=path)
        else:
            logger.info("Running in stdio mode")
            mcp.run(transport="stdio")

    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.exception(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
