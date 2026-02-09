"""
Tests for Deep Research MCP tools.
"""

import asyncio
import json
import os

import pytest


class _TextOutput:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Interaction:
    def __init__(self, status: str, text: str = ""):
        self.status = status
        self.outputs = [_TextOutput(text)] if text else []

    def model_dump(self, exclude_none=True):  # pragma: no cover - debug fallback
        return {"status": self.status}


@pytest.fixture(autouse=True)
def setup_and_teardown(tmp_path):
    """Isolate DB state per test."""
    import webtx_mcp.db as db_module
    import webtx_mcp.key_manager as key_manager

    db_module.reset_db()
    key_manager.KeyManager._instance = None
    os.environ["WEBTX_MCP_DB_PATH"] = str(tmp_path / "test.db")

    yield

    db_module.reset_db()
    key_manager.KeyManager._instance = None
    if "WEBTX_MCP_DB_PATH" in os.environ:
        del os.environ["WEBTX_MCP_DB_PATH"]


def test_research_start_creates_job(monkeypatch, tmp_path):
    import webtx_mcp.server as server
    from webtx_mcp.research_jobs import get_job

    async def fake_start(question: str, thinking_summaries: str = "auto"):
        return "int-100", "in_progress"

    monkeypatch.setattr(server, "start_gemini_deep_research", fake_start)

    output = tmp_path / "deep.md"
    raw = asyncio.run(
        server.research_gemini_start.fn(
            question="Explain MCP briefly",
            output_path=str(output),
            thinking_summaries="auto",
        )
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["action"] == "start"
    assert payload["interaction_id"] == "int-100"
    assert payload["status"] == "in_progress"
    assert payload["output_path"] == str(output)

    job = get_job("int-100")
    assert job is not None
    assert job["output_path"] == str(output)


def test_research_status_writes_file_and_is_idempotent(monkeypatch, tmp_path):
    import webtx_mcp.server as server
    from webtx_mcp.research_jobs import create_job, get_job

    output = tmp_path / "result.md"
    create_job(
        interaction_id="int-200",
        question="Q",
        output_path=str(output),
        status="in_progress",
    )

    async def fake_get(interaction_id: str):
        return _Interaction(status="completed", text="final answer")

    monkeypatch.setattr(server, "get_gemini_interaction", fake_get)

    raw1 = asyncio.run(server.research_gemini_status.fn(interaction_id="int-200"))
    p1 = json.loads(raw1)
    assert p1["ok"] is True
    assert p1["status"] == "completed"
    assert p1["saved"] is True
    assert p1["output_chars"] == len("final answer")
    assert output.read_text(encoding="utf-8") == "final answer"

    raw2 = asyncio.run(server.research_gemini_status.fn(interaction_id="int-200"))
    p2 = json.loads(raw2)
    assert p2["ok"] is True
    assert p2["status"] == "completed"
    assert p2["saved"] is True
    assert p2["output_chars"] == len("final answer")
    assert output.read_text(encoding="utf-8") == "final answer"

    job = get_job("int-200")
    assert job is not None
    assert job["saved_at"] is not None


def test_research_status_not_found():
    import webtx_mcp.server as server

    raw = asyncio.run(server.research_gemini_status.fn(interaction_id="missing"))
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["action"] == "status"
    assert payload["status"] == "not_found"


def test_research_cancel_updates_status(monkeypatch, tmp_path):
    import webtx_mcp.server as server
    from webtx_mcp.research_jobs import create_job, get_job

    create_job(
        interaction_id="int-300",
        question="Q",
        output_path=str(tmp_path / "cancel.md"),
        status="in_progress",
    )

    async def fake_cancel(interaction_id: str):
        return "cancelled", _Interaction(status="cancelled")

    monkeypatch.setattr(server, "cancel_gemini_interaction", fake_cancel)

    raw = asyncio.run(server.research_gemini_cancel.fn(interaction_id="int-300"))
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["action"] == "cancel"
    assert payload["status"] == "cancelled"

    job = get_job("int-300")
    assert job is not None
    assert job["status"] == "cancelled"


def test_ask_gemini_invalid_model_returns_error(monkeypatch):
    import webtx_mcp.server as server

    called = {"query": False}

    async def fake_query(**kwargs):
        called["query"] = True
        return "should-not-run"

    monkeypatch.setattr(server, "query_gemini", fake_query)

    raw = asyncio.run(
        server.ask_gemini.fn(
            question="Hello",
            model="deep_research",
        )
    )

    assert raw == "Error: Invalid model. Use one of: flash, pro"
    assert called["query"] is False


def test_research_cancel_idempotent_when_remote_completed(monkeypatch, tmp_path):
    import webtx_mcp.server as server
    from webtx_mcp.research_jobs import create_job, get_job

    create_job(
        interaction_id="int-301",
        question="Q",
        output_path=str(tmp_path / "done.md"),
        status="in_progress",
    )

    async def fake_cancel(interaction_id: str):
        return "Error: Interaction already completed"

    async def fake_get(interaction_id: str):
        return _Interaction(status="completed", text="final")

    monkeypatch.setattr(server, "cancel_gemini_interaction", fake_cancel)
    monkeypatch.setattr(server, "get_gemini_interaction", fake_get)

    raw = asyncio.run(server.research_gemini_cancel.fn(interaction_id="int-301"))
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["action"] == "cancel"
    assert payload["status"] == "completed"
    assert "warning" in payload

    job = get_job("int-301")
    assert job is not None
    assert job["status"] == "completed"


def test_research_cancel_error_does_not_force_failed_status(monkeypatch, tmp_path):
    import webtx_mcp.server as server
    from webtx_mcp.research_jobs import create_job, get_job

    create_job(
        interaction_id="int-302",
        question="Q",
        output_path=str(tmp_path / "still.md"),
        status="in_progress",
    )

    async def fake_cancel(interaction_id: str):
        return "Error: transient cancel failure"

    async def fake_get(interaction_id: str):
        return "Error: could not fetch remote status"

    monkeypatch.setattr(server, "cancel_gemini_interaction", fake_cancel)
    monkeypatch.setattr(server, "get_gemini_interaction", fake_get)

    raw = asyncio.run(server.research_gemini_cancel.fn(interaction_id="int-302"))
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["action"] == "cancel"
    assert payload["status"] == "in_progress"
    assert "transient cancel failure" in payload["error"]

    job = get_job("int-302")
    assert job is not None
    assert job["status"] == "in_progress"
    assert "transient cancel failure" in (job["last_error"] or "")
