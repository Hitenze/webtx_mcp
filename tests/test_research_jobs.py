"""
Tests for Deep Research SQLite job store.
"""

import os

import pytest


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


def test_create_and_get_job():
    from webtx_mcp.research_jobs import create_job, get_job

    create_job(
        interaction_id="int-1",
        question="What is MCP?",
        output_path="/tmp/out.md",
        status="in_progress",
    )

    job = get_job("int-1")
    assert job is not None
    assert job["interaction_id"] == "int-1"
    assert job["status"] == "in_progress"
    assert job["output_path"] == "/tmp/out.md"
    assert job["model"] == "deep_research"


def test_update_status_and_mark_saved():
    from webtx_mcp.research_jobs import (
        create_job,
        get_job,
        mark_saved,
        set_error,
        update_status,
    )

    create_job(
        interaction_id="int-2",
        question="Q",
        output_path="/tmp/out2.md",
        status="in_progress",
    )

    update_status("int-2", "requires_action")
    job = get_job("int-2")
    assert job is not None
    assert job["status"] == "requires_action"

    set_error("int-2", "some error", status="failed")
    job = get_job("int-2")
    assert job is not None
    assert job["status"] == "failed"
    assert "some error" in (job["last_error"] or "")

    mark_saved("int-2", output_chars=123, status="completed")
    job = get_job("int-2")
    assert job is not None
    assert job["status"] == "completed"
    assert job["output_chars"] == 123
    assert job["saved_at"] is not None


def test_cleanup_old_jobs():
    from webtx_mcp.db import get_db
    from webtx_mcp.research_jobs import cleanup_old_jobs, create_job, get_job

    create_job(
        interaction_id="int-old",
        question="Q-old",
        output_path="/tmp/old.md",
        status="completed",
    )
    create_job(
        interaction_id="int-new",
        question="Q-new",
        output_path="/tmp/new.md",
        status="in_progress",
    )

    conn = get_db().get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE research_jobs
        SET updated_at = datetime('now', '-40 days')
        WHERE interaction_id = 'int-old'
        """
    )
    conn.commit()

    deleted = cleanup_old_jobs(days=30)
    assert deleted == 1
    assert get_job("int-old") is None
    assert get_job("int-new") is not None
