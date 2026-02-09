"""
FastMCP server for webtx-mcp.
Provides 5 tools: ask_gemini, research_gemini, api_add_key, api_list_keys, api_remove_key.
"""

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
    query_gemini,
    query_gemini_deep_research,
)
from .key_manager import get_key_manager

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
        model: Model to use - "pro" for gemini-3-pro-preview,
               "flash" for gemini-3-flash-preview (default: "flash")
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

        logger.info(f"[Gemini] Processing question: {question[:100]}...")

        # Map model string
        gemini_model = GEMINI_PRO if model.lower() == "pro" else GEMINI_FLASH

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
        "Run Gemini research and save the full response to a file. "
        "Supports flash, pro, and deep_research. "
        "Deep Research uses background interactions with polling."
    )
)
async def research_gemini(
    question: str,
    output_path: str,
    model: str = "deep_research",
    thinking: str = "medium",
    temperature: float = 0.7,
    google_search: bool = True,
    poll_interval_seconds: int = 10,
    timeout_seconds: int = 1800,
    thinking_summaries: str = "auto",
) -> str:
    """
    Run Gemini research and persist result text to a file.

    Execution mode:
    - For model='deep_research', runs an asynchronous workflow internally
      (Gemini background interaction + polling).
    - Returns in a single MCP call; host-side MCP tool timeout still applies.

    Args:
        question: The question or prompt to research
        output_path: File path to write the final report
        model: "deep_research" (default), "pro", or "flash"
        thinking: Thinking level for flash/pro - "none", "low", "medium", "high"
        temperature: Sampling temperature for flash/pro (0.0-1.0)
        google_search: Enable Google Search for flash/pro calls
        poll_interval_seconds: Deep Research polling interval in seconds
        timeout_seconds: Deep Research timeout in seconds
        thinking_summaries: Deep Research summaries - "auto" or "none"

    Returns:
        Status text including output path and interaction id when available
    """
    try:
        if not question or not question.strip():
            return "Error: Question cannot be empty"

        if len(question) > 10000:
            return "Error: Question too long (max 10000 characters)"

        if not output_path or not output_path.strip():
            return "Error: output_path cannot be empty"

        model_key = (model or "").strip().lower()
        if model_key not in ("flash", "pro", "deep_research"):
            return "Error: Invalid model. Use one of: flash, pro, deep_research"

        if poll_interval_seconds <= 0:
            return "Error: poll_interval_seconds must be > 0"
        if timeout_seconds <= 0:
            return "Error: timeout_seconds must be > 0"

        logger.info(f"[Research] Processing question: {question[:100]}...")
        response_text = ""
        interaction_id = "n/a"

        if model_key == "deep_research":
            result = await query_gemini_deep_research(
                question=question,
                poll_interval_seconds=poll_interval_seconds,
                timeout_seconds=timeout_seconds,
                thinking_summaries=thinking_summaries,
            )
            if isinstance(result, str) and result.startswith("Error:"):
                logger.error(f"[Research] Error: {result}")
                return result

            interaction_id, response_text = result
        else:
            gemini_model = GEMINI_PRO if model_key == "pro" else GEMINI_FLASH
            thinking_level = thinking.upper()
            if thinking_level not in ("NONE", "LOW", "MEDIUM", "HIGH"):
                thinking_level = "MEDIUM"

            response_text = await query_gemini(
                question=question,
                model=gemini_model,
                thinking_level=thinking_level,
                temperature=temperature,
                google_search=google_search,
            )
            if isinstance(response_text, str) and response_text.startswith("Error:"):
                logger.error(f"[Research] Error: {response_text}")
                return response_text

        target_path = _resolve_output_path(output_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(response_text, encoding="utf-8")

        logger.info(
            "[Research] Saved response (%s chars) to %s",
            len(response_text),
            target_path,
        )
        return (
            "Saved Gemini research output to "
            f"{target_path} (model={model_key}, interaction_id={interaction_id}, "
            f"chars={len(response_text)})"
        )

    except Exception as e:
        logger.exception(f"[Research] Unexpected error: {e}")
        return f"Error: {str(e)}"


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
        "Starting webtx-mcp server with 5 tools: "
        "ask_gemini, research_gemini, api_add_key, api_list_keys, api_remove_key"
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
