"""
SQLite-backed job store for Deep Research interactions.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .db import get_db

ENDED_STATUSES = ("completed", "failed", "cancelled")


def create_job(
    interaction_id: str,
    question: str,
    output_path: str,
    status: str,
    model: str = "deep_research",
) -> None:
    """Create or update a research job record."""
    conn = get_db().get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO research_jobs (
            interaction_id, question, output_path, model, status, last_error, output_chars
        )
        VALUES (?, ?, ?, ?, ?, NULL, 0)
        ON CONFLICT(interaction_id) DO UPDATE SET
            question = excluded.question,
            output_path = excluded.output_path,
            model = excluded.model,
            status = excluded.status,
            last_error = NULL,
            output_chars = 0,
            saved_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        """,
        (interaction_id, question, output_path, model, status),
    )
    conn.commit()


def get_job(interaction_id: str) -> Optional[Dict[str, Any]]:
    """Get a research job by interaction ID."""
    conn = get_db().get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT interaction_id, question, output_path, model, status, last_error,
               output_chars, created_at, updated_at, saved_at
        FROM research_jobs
        WHERE interaction_id = ?
        """,
        (interaction_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def update_status(interaction_id: str, status: str) -> None:
    """Update status and touch updated_at for a job."""
    conn = get_db().get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE research_jobs
        SET status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE interaction_id = ?
        """,
        (status, interaction_id),
    )
    conn.commit()


def mark_saved(interaction_id: str, output_chars: int, status: str = "completed") -> None:
    """Mark output as written to disk."""
    conn = get_db().get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE research_jobs
        SET status = ?,
            output_chars = ?,
            last_error = NULL,
            saved_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE interaction_id = ?
        """,
        (status, output_chars, interaction_id),
    )
    conn.commit()


def set_error(interaction_id: str, error: str, status: str = "failed") -> None:
    """Record an error message and update status."""
    conn = get_db().get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE research_jobs
        SET status = ?,
            last_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE interaction_id = ?
        """,
        (status, error, interaction_id),
    )
    conn.commit()


def set_error_only(interaction_id: str, error: str) -> None:
    """Record an error message without changing job status."""
    conn = get_db().get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE research_jobs
        SET last_error = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE interaction_id = ?
        """,
        (error, interaction_id),
    )
    conn.commit()


def cleanup_old_jobs(days: int = 30) -> int:
    """Delete ended jobs older than the given retention window."""
    days = max(1, int(days))
    conn = get_db().get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        DELETE FROM research_jobs
        WHERE status IN (?, ?, ?)
          AND updated_at < datetime('now', ?)
        """,
        (*ENDED_STATUSES, f"-{days} days"),
    )
    conn.commit()
    return cursor.rowcount
