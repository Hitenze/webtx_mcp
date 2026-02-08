#!/usr/bin/env python3
"""
Main entry point for running the webtx-mcp server.

Usage:
    python -m webtx_mcp              # Start MCP server (stdio)
    python -m webtx_mcp --onboard    # Interactive API key setup
"""

import argparse


def main():
    parser = argparse.ArgumentParser(
        description="webtx-mcp — Lightweight Gemini MCP server"
    )
    parser.add_argument(
        "--onboard",
        action="store_true",
        help="Run interactive API key setup",
    )
    args = parser.parse_args()

    if args.onboard:
        from .onboard import run_onboard

        run_onboard()
        return

    from .server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
