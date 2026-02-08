"""
FastMCP server for webtx-mcp.
Provides 4 tools: ask_gemini, api_add_key, api_list_keys, api_remove_key.
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

from .gemini_client import query_gemini, GEMINI_PRO, GEMINI_FLASH
from .key_manager import get_key_manager

# Create the FastMCP server instance
mcp = FastMCP("webtx-mcp")


# ============================================================
# Tools
# ============================================================


@mcp.tool(description="Send a question to Gemini AI with web search and reasoning")
async def ask_gemini(
    question: str,
    model: str = "flash",
    thinking: str = "medium",
    temperature: float = 0.7,
    google_search: bool = True,
) -> str:
    """
    Send a question to Gemini AI with configurable parameters.

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
        "Starting webtx-mcp server with 4 tools: "
        "ask_gemini, api_add_key, api_list_keys, api_remove_key"
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
