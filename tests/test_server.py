"""
Tests for webtx-mcp server tool registration.
"""

import pytest


def test_tool_registration():
    """Test that all 5 tools are registered on the FastMCP instance."""
    from webtx_mcp.server import mcp

    # Get all registered tool names
    tool_names = set()
    for tool in mcp._tool_manager._tools.values():
        tool_names.add(tool.name)

    expected = {
        "ask_gemini",
        "research_gemini",
        "api_add_key",
        "api_list_keys",
        "api_remove_key",
    }
    assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"


def test_tool_count():
    """Test exactly 5 tools are registered."""
    from webtx_mcp.server import mcp

    tool_count = len(mcp._tool_manager._tools)
    assert tool_count == 5, f"Expected 5 tools, got {tool_count}"
