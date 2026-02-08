"""
Gemini API client for webtx-mcp.
Simplified from CPS-MCP: no caching, no RAG, no FTS5.
"""

import logging
from typing import Optional, Tuple, Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Available Gemini models
GEMINI_PRO = "gemini-3-pro-preview"
GEMINI_FLASH = "gemini-3-flash-preview"
GEMINI_FALLBACK = GEMINI_FLASH


def get_gemini_client() -> Tuple[genai.Client, int]:
    """
    Get Gemini client using KeyManager for API key.

    Returns:
        Tuple of (genai.Client, key_id) where key_id is -1 for env keys
    """
    from .key_manager import get_key_manager

    manager = get_key_manager()
    key = manager.get_key()
    if not key:
        raise ValueError(
            "No Google API key available. "
            "Add a key with api_add_key tool or set GOOGLE_API_KEY in .env file."
        )
    return genai.Client(api_key=key.key), key.id


async def _call_gemini(
    client: genai.Client,
    model: str,
    prompt: str,
    config: types.GenerateContentConfig,
) -> Any:
    """Internal helper to call Gemini API."""
    return await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config=config,
    )


async def query_gemini(
    question: str,
    model: str = GEMINI_FLASH,
    thinking_level: str = "MEDIUM",
    temperature: float = 0.7,
    google_search: bool = True,
) -> str:
    """
    Query Gemini API with full configuration support.

    Args:
        question: The user prompt to send
        model: Gemini model ID
        thinking_level: "NONE", "LOW", "MEDIUM", "HIGH"
        temperature: Sampling temperature (0.0-1.0)
        google_search: Whether to enable GoogleSearch tool

    Returns:
        The model's response as a string
    """
    from .key_manager import get_key_manager

    client, key_id = get_gemini_client()
    manager = get_key_manager()

    # Build config
    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=16384,
    )

    # Add thinking config for non-NONE levels
    if thinking_level and thinking_level.upper() != "NONE":
        config.thinking_config = types.ThinkingConfig(
            thinking_level=thinking_level,
        )

    # Add GoogleSearch tool if requested
    if google_search:
        config.tools = [
            types.Tool(google_search=types.GoogleSearch()),
        ]

    # Try primary model, fallback on 429
    models_to_try = [model]
    if model != GEMINI_FALLBACK:
        models_to_try.append(GEMINI_FALLBACK)

    last_error = None
    for current_model in models_to_try:
        try:
            response = await _call_gemini(client, current_model, question, config)

            if response.text:
                used_fallback = current_model != model
                manager.report_success(key_id)

                if used_fallback:
                    logger.info(
                        f"Used fallback model {current_model} due to quota exhaustion"
                    )
                    fallback_notice = (
                        f"[Note: Used fallback model {current_model} "
                        f"due to Pro quota limits]\n\n"
                    )
                    return fallback_notice + response.text

                return response.text
            else:
                return "Error: Received empty response from Gemini API."

        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                manager.report_failure(key_id, 429)
                logger.warning(
                    f"Quota exhausted for {current_model}, trying fallback..."
                )
                last_error = e
                continue
            else:
                if "401" in error_str or "UNAUTHENTICATED" in error_str:
                    manager.report_failure(key_id, 401)
                elif "403" in error_str or "PERMISSION_DENIED" in error_str:
                    manager.report_failure(key_id, 403)
                else:
                    manager.report_failure(key_id, 500)
                logger.error(f"Gemini API error: {e}")
                return f"Error: Gemini API call failed - {error_str}"

    # All models failed with quota error
    logger.error(f"All models exhausted quota: {last_error}")
    return (
        f"Error: Gemini API call failed - all models quota exhausted. "
        f"{str(last_error)}"
    )
