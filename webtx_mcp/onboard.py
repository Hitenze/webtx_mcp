"""Interactive onboarding for API key configuration."""

import sys

from .key_manager import get_key_manager


def print_banner() -> None:
    print()
    print("=" * 60)
    print("                    webtx-mcp Onboard")
    print("=" * 60)
    print()


def handle_existing_keys(km, existing: list) -> None:
    print(f"  Detected {len(existing)} existing API key(s):")
    for key in existing:
        name = key.get("name", "unnamed")
        usage = key.get("usage_count", 0)
        print(f"   - {name} (used: {usage})")

    print()
    try:
        response = input("Clear all keys and start fresh? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nOnboard cancelled.")
        sys.exit(0)

    if response == "y":
        for key in existing:
            km.remove_key(key["id"])
        print("  All API keys cleared.")
    else:
        print("  Keeping existing keys.")


def configure_google(km) -> int:
    """Configure Google API keys. Returns number of keys added."""
    print()
    print("=" * 60)
    print("  Google Gemini API Key")
    print("   Required for ask_gemini tool")
    print("   Get key: https://makersuite.google.com/app/apikey")
    print("=" * 60)

    key_count = 0
    while True:
        try:
            key = input("\nEnter Google API key (or 'skip' to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nOnboard cancelled.")
            sys.exit(0)

        if key.lower() == "skip" or not key:
            if key_count == 0:
                print("   Skipped Google Gemini")
            break

        try:
            limit_str = input(
                "Monthly limit (0=unlimited, press Enter for 0): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nOnboard cancelled.")
            sys.exit(0)

        monthly_limit = 0
        if limit_str:
            try:
                monthly_limit = int(limit_str)
                if monthly_limit < 0:
                    print("   Limit cannot be negative, using 0 (unlimited)")
                    monthly_limit = 0
            except ValueError:
                print("   Invalid number, using 0 (unlimited)")
                monthly_limit = 0

        key_count += 1
        result = km.add_key(
            key=key,
            name=f"onboard-{key_count}",
            monthly_limit=monthly_limit,
        )

        if result.get("success"):
            limit_display = monthly_limit if monthly_limit > 0 else "unlimited"
            print(f"   Added (ID: {result['key_id']}, limit: {limit_display})")
        else:
            error = result.get("error", "unknown error")
            print(f"   Failed to add: {error}")
            key_count -= 1
            continue

        try:
            more = input("\nAdd another Google key? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nOnboard cancelled.")
            sys.exit(0)

        if more != "y":
            break

    return key_count


def print_summary(total_keys: int) -> None:
    print()
    print("=" * 60)
    print("  Onboard Complete!")
    print("=" * 60)
    print()
    print(f"   Google Gemini: {total_keys} key(s)")
    print()
    if total_keys > 0:
        print("  Run MCP server: uv run python -m webtx_mcp")
    else:
        print("  No API keys configured.")
        print("  You can run onboard again or set GOOGLE_API_KEY in .env")
    print()


def run_onboard() -> None:
    """Main onboard entry point."""
    print_banner()

    km = get_key_manager()

    existing = km.list_keys()
    if existing:
        handle_existing_keys(km, existing)

    total = configure_google(km)
    print_summary(total)
