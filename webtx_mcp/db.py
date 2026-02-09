"""
Minimal SQLite database for webtx-mcp.
Stores API keys and usage logs only.
"""

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".webtx_mcp" / "webtx.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL DEFAULT 'google',
    key_hash TEXT NOT NULL,
    encrypted_key TEXT NOT NULL,
    name TEXT,
    monthly_limit INTEGER DEFAULT 0,
    daily_limit INTEGER DEFAULT 0,
    usage_count INTEGER DEFAULT 0,
    daily_usage INTEGER DEFAULT 0,
    total_usage INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    suspended_until DATETIME,
    consecutive_failures INTEGER DEFAULT 0,
    last_used_at DATETIME,
    last_reset_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(service, key_hash)
);

CREATE TABLE IF NOT EXISTS api_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id INTEGER NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    status_code INTEGER,
    error_type TEXT,
    FOREIGN KEY(key_id) REFERENCES api_keys(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS research_jobs (
    interaction_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    output_path TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'deep_research',
    status TEXT NOT NULL,
    last_error TEXT,
    output_chars INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    saved_at DATETIME
);
"""


class DB:
    """Thread-safe singleton SQLite database."""

    _instance: Optional["DB"] = None
    _instance_lock = threading.Lock()
    _connection: Optional[sqlite3.Connection] = None

    def __new__(cls) -> "DB":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

    def get_connection(self) -> sqlite3.Connection:
        """Get or create the SQLite connection (thread-safe)."""
        if self._connection is None:
            db_path = Path(os.getenv("WEBTX_MCP_DB_PATH", str(DEFAULT_DB_PATH)))
            db_path.parent.mkdir(parents=True, exist_ok=True)

            self._connection = sqlite3.connect(str(db_path), check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")

            # Create tables
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

            logger.info(f"[DB] Connected to {db_path}")

        return self._connection

    def close(self) -> None:
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None


# Module-level accessors


def get_db() -> DB:
    """Get the singleton DB instance."""
    return DB()


def reset_db() -> None:
    """Reset the DB singleton (for testing)."""
    with DB._instance_lock:
        if DB._instance is not None:
            if DB._connection is not None:
                try:
                    DB._connection.close()
                except Exception:
                    pass
                DB._connection = None
            if hasattr(DB._instance, "_initialized"):
                delattr(DB._instance, "_initialized")
        DB._instance = None
