"""
API Key Manager for webtx-mcp.
Provides multi-key load balancing, automatic error handling, and lazy monthly reset.
Simplified from CPS-MCP: single service (google) only.
"""

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

# Load .env from project root (parent of webtx_mcp package)
project_root = Path(__file__).parent.parent
load_dotenv(project_root / ".env")

logger = logging.getLogger(__name__)

# Master key for API key encryption
_MASTER_KEY_PATH = Path.home() / ".webtx_mcp" / ".master_key"


def _get_or_create_master_key() -> bytes:
    """Get or create the master encryption key."""
    if _MASTER_KEY_PATH.exists():
        return _MASTER_KEY_PATH.read_bytes().strip()

    key = Fernet.generate_key()
    _MASTER_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MASTER_KEY_PATH.write_bytes(key)
    _MASTER_KEY_PATH.chmod(0o600)
    logger.info("[KeyManager] Generated new master encryption key")
    return key


def _get_fernet() -> Fernet:
    """Get Fernet instance with master key."""
    return Fernet(_get_or_create_master_key())


def _encrypt_key(plaintext: str) -> str:
    """Encrypt an API key. Returns base64 Fernet token as string."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def _decrypt_key(stored: str) -> str:
    """Decrypt an API key. Falls back to plaintext if decryption fails."""
    try:
        f = _get_fernet()
        return f.decrypt(stored.encode()).decode()
    except (InvalidToken, Exception):
        return stored


# Status values
STATUS_ACTIVE = "active"
STATUS_SUSPENDED = "suspended"
STATUS_DISABLED = "disabled"

# Error handling configuration
MAX_CONSECUTIVE_FAILURES = 5
SUSPENSION_DURATION_MINUTES = 15


@dataclass
class APIKey:
    """Represents an API key with its metadata."""

    id: int
    service: str
    key: str
    name: str
    monthly_limit: int
    daily_limit: int
    usage_count: int
    daily_usage: int
    total_usage: int
    status: str
    suspended_until: Optional[datetime]
    consecutive_failures: int
    last_used_at: Optional[datetime]
    last_reset_at: datetime

    @property
    def usage_ratio(self) -> float:
        """Calculate usage ratio for load balancing."""
        if self.monthly_limit == 0:
            return self.usage_count / 1000000.0
        return self.usage_count / self.monthly_limit

    @property
    def is_available(self) -> bool:
        """Check if key is available for use."""
        if self.status == STATUS_DISABLED:
            return False
        if self.status == STATUS_SUSPENDED:
            if self.suspended_until and datetime.now() < self.suspended_until:
                return False
        if self.monthly_limit > 0 and self.usage_count >= self.monthly_limit:
            return False
        if self.daily_limit > 0 and self.daily_usage >= self.daily_limit:
            return False
        return True


class KeyManager:
    """
    API Key Manager - Thread-safe singleton pattern.

    Provides:
    - Multi-key load balancing (usage ratio based)
    - Automatic error handling (429 -> suspend, 401 -> disable)
    - Lazy monthly/daily reset (no cron needed)
    - Fallback to .env GOOGLE_API_KEY if no DB keys available
    """

    _instance: Optional["KeyManager"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "KeyManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        from .db import get_db

        self._db = get_db()

    def _hash_key(self, key: str) -> str:
        """Hash API key for deduplication."""
        return hashlib.sha256(key.encode()).hexdigest()

    def _lazy_reset(self, conn) -> None:
        """Perform lazy monthly and daily reset."""
        cursor = conn.cursor()
        now = datetime.now()

        # Monthly reset
        cursor.execute(
            """
            UPDATE api_keys
            SET usage_count = 0,
                last_reset_at = ?
            WHERE strftime('%Y-%m', last_reset_at) != strftime('%Y-%m', ?)
            """,
            (now.isoformat(), now.isoformat()),
        )

        # Daily reset
        cursor.execute(
            """
            UPDATE api_keys
            SET daily_usage = 0
            WHERE date(last_used_at) != date(?)
              AND last_used_at IS NOT NULL
            """,
            (now.isoformat(),),
        )

        # Auto-unsuspend expired keys
        cursor.execute(
            """
            UPDATE api_keys
            SET status = 'active',
                consecutive_failures = 0
            WHERE status = 'suspended'
              AND suspended_until IS NOT NULL
              AND datetime(suspended_until) <= datetime(?)
            """,
            (now.isoformat(),),
        )

        conn.commit()

    def get_key(self) -> Optional[APIKey]:
        """
        Get the best available Google API key.

        1. Lazy reset monthly/daily counts
        2. Filter out disabled/suspended/over-limit keys
        3. Select key with lowest usage_ratio
        4. Fall back to .env GOOGLE_API_KEY if no DB keys available
        """
        conn = self._db.get_connection()
        cursor = conn.cursor()

        self._lazy_reset(conn)

        cursor.execute(
            """
            SELECT id, service, encrypted_key, name, monthly_limit, daily_limit,
                   usage_count, daily_usage, total_usage, status, suspended_until,
                   consecutive_failures, last_used_at, last_reset_at
            FROM api_keys
            WHERE service = 'google'
            ORDER BY id
            """
        )
        rows = cursor.fetchall()

        available_keys: List[APIKey] = []
        for row in rows:
            suspended_until = None
            if row["suspended_until"]:
                try:
                    suspended_until = datetime.fromisoformat(row["suspended_until"])
                except ValueError:
                    pass

            last_used_at = None
            if row["last_used_at"]:
                try:
                    last_used_at = datetime.fromisoformat(row["last_used_at"])
                except ValueError:
                    pass

            last_reset_at = datetime.now()
            if row["last_reset_at"]:
                try:
                    last_reset_at = datetime.fromisoformat(row["last_reset_at"])
                except ValueError:
                    pass

            key = APIKey(
                id=row["id"],
                service=row["service"],
                key=_decrypt_key(row["encrypted_key"]),
                name=row["name"] or "",
                monthly_limit=row["monthly_limit"] or 0,
                daily_limit=row["daily_limit"] or 0,
                usage_count=row["usage_count"] or 0,
                daily_usage=row["daily_usage"] or 0,
                total_usage=row["total_usage"] or 0,
                status=row["status"] or STATUS_ACTIVE,
                suspended_until=suspended_until,
                consecutive_failures=row["consecutive_failures"] or 0,
                last_used_at=last_used_at,
                last_reset_at=last_reset_at,
            )

            if key.is_available:
                available_keys.append(key)

        if available_keys:
            best_key = min(available_keys, key=lambda k: k.usage_ratio)
            logger.debug(
                f"[KeyManager] Selected key {best_key.id} ({best_key.name}) "
                f"with usage ratio {best_key.usage_ratio:.4f}"
            )
            return best_key

        # Fallback to .env
        return self._get_env_key()

    def _get_env_key(self) -> Optional[APIKey]:
        """Get API key from GOOGLE_API_KEY environment variable as fallback."""
        key = os.getenv("GOOGLE_API_KEY")
        if not key:
            logger.warning("[KeyManager] No Google API key available")
            return None

        logger.debug("[KeyManager] Using .env fallback for google")
        return APIKey(
            id=-1,
            service="google",
            key=key,
            name=".env (GOOGLE_API_KEY)",
            monthly_limit=0,
            daily_limit=0,
            usage_count=0,
            daily_usage=0,
            total_usage=0,
            status=STATUS_ACTIVE,
            suspended_until=None,
            consecutive_failures=0,
            last_used_at=None,
            last_reset_at=datetime.now(),
        )

    def report_success(self, key_id: int) -> None:
        """Report successful API call."""
        if key_id < 0:
            return

        conn = self._db.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE api_keys
            SET usage_count = usage_count + 1,
                daily_usage = daily_usage + 1,
                total_usage = total_usage + 1,
                consecutive_failures = 0,
                last_used_at = ?
            WHERE id = ?
            """,
            (now, key_id),
        )

        cursor.execute(
            """
            INSERT INTO api_usage_log (key_id, status_code, error_type)
            VALUES (?, 200, 'success')
            """,
            (key_id,),
        )

        conn.commit()
        logger.debug(f"[KeyManager] Reported success for key {key_id}")

    def report_failure(self, key_id: int, http_code: int) -> None:
        """
        Report failed API call.

        Actions based on HTTP code:
        - 429: Suspend key for 15 minutes
        - 401/403: Disable key permanently
        - Other: Increment consecutive_failures, suspend if >= 5
        """
        if key_id < 0:
            logger.warning(f"[KeyManager] Env key got error {http_code}")
            return

        conn = self._db.get_connection()
        cursor = conn.cursor()
        now = datetime.now()

        # Determine error type
        if http_code == 429:
            error_type = "rate_limit"
        elif http_code in (401, 403):
            error_type = "auth"
        elif http_code >= 500:
            error_type = "server"
        elif http_code >= 400:
            error_type = "client"
        else:
            error_type = "timeout"

        # Log the failure
        cursor.execute(
            """
            INSERT INTO api_usage_log (key_id, status_code, error_type)
            VALUES (?, ?, ?)
            """,
            (key_id, http_code, error_type),
        )

        if http_code == 429:
            suspended_until = now + timedelta(minutes=SUSPENSION_DURATION_MINUTES)
            cursor.execute(
                """
                UPDATE api_keys
                SET status = 'suspended',
                    suspended_until = ?,
                    consecutive_failures = consecutive_failures + 1,
                    last_used_at = ?
                WHERE id = ?
                """,
                (suspended_until.isoformat(), now.isoformat(), key_id),
            )
            logger.warning(
                f"[KeyManager] Key {key_id} suspended until {suspended_until} (429)"
            )

        elif http_code in (401, 403):
            cursor.execute(
                """
                UPDATE api_keys
                SET status = 'disabled',
                    consecutive_failures = consecutive_failures + 1,
                    last_used_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), key_id),
            )
            logger.error(
                f"[KeyManager] Key {key_id} disabled permanently ({http_code})"
            )

        else:
            cursor.execute(
                """
                UPDATE api_keys
                SET consecutive_failures = consecutive_failures + 1,
                    last_used_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), key_id),
            )

            cursor.execute(
                "SELECT consecutive_failures FROM api_keys WHERE id = ?", (key_id,)
            )
            row = cursor.fetchone()
            if row and row["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
                suspended_until = now + timedelta(minutes=SUSPENSION_DURATION_MINUTES)
                cursor.execute(
                    """
                    UPDATE api_keys
                    SET status = 'suspended',
                        suspended_until = ?
                    WHERE id = ?
                    """,
                    (suspended_until.isoformat(), key_id),
                )
                logger.warning(
                    f"[KeyManager] Key {key_id} suspended (circuit breaker: "
                    f"{row['consecutive_failures']} failures)"
                )

        conn.commit()

    def add_key(
        self,
        key: str,
        name: str = "",
        monthly_limit: int = 0,
        daily_limit: int = 0,
    ) -> Dict[str, Any]:
        """
        Add a new Google API key.

        Args:
            key: The API key value
            name: Optional display name
            monthly_limit: Monthly usage limit (0 = unlimited)
            daily_limit: Daily usage limit (0 = unlimited)
        """
        if not key or not key.strip():
            return {"success": False, "error": "API key cannot be empty"}

        if monthly_limit < 0:
            return {"success": False, "error": "monthly_limit cannot be negative"}
        if daily_limit < 0:
            return {"success": False, "error": "daily_limit cannot be negative"}

        key = key.strip()
        key_hash = self._hash_key(key)

        conn = self._db.get_connection()
        cursor = conn.cursor()

        try:
            encrypted = _encrypt_key(key)
            cursor.execute(
                """
                INSERT INTO api_keys (service, key_hash, encrypted_key, name, monthly_limit, daily_limit)
                VALUES ('google', ?, ?, ?, ?, ?)
                """,
                (key_hash, encrypted, name, monthly_limit, daily_limit),
            )
            conn.commit()
            key_id = cursor.lastrowid
            logger.info(f"[KeyManager] Added key {key_id} for google")
            return {"success": True, "key_id": key_id}

        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                return {"success": False, "error": "Key already exists"}
            logger.error(f"[KeyManager] Failed to add key: {e}")
            return {"success": False, "error": str(e)}

    def list_keys(self) -> List[Dict[str, Any]]:
        """List all Google API keys with usage stats."""
        conn = self._db.get_connection()
        cursor = conn.cursor()

        self._lazy_reset(conn)

        cursor.execute(
            """
            SELECT id, service, name, monthly_limit, daily_limit,
                   usage_count, daily_usage, total_usage, status,
                   suspended_until, consecutive_failures, last_used_at, last_reset_at
            FROM api_keys
            WHERE service = 'google'
            ORDER BY id
            """
        )

        results = []
        for row in cursor.fetchall():
            usage_ratio = 0.0
            if row["monthly_limit"] and row["monthly_limit"] > 0:
                usage_ratio = (row["usage_count"] or 0) / row["monthly_limit"]

            results.append(
                {
                    "id": row["id"],
                    "service": row["service"],
                    "name": row["name"] or "",
                    "monthly_limit": row["monthly_limit"] or 0,
                    "daily_limit": row["daily_limit"] or 0,
                    "usage_count": row["usage_count"] or 0,
                    "daily_usage": row["daily_usage"] or 0,
                    "total_usage": row["total_usage"] or 0,
                    "status": row["status"] or STATUS_ACTIVE,
                    "usage_ratio": round(usage_ratio, 4),
                    "suspended_until": row["suspended_until"],
                    "consecutive_failures": row["consecutive_failures"] or 0,
                    "last_used_at": row["last_used_at"],
                    "last_reset_at": row["last_reset_at"],
                }
            )

        return results

    def remove_key(self, key_id: int) -> Dict[str, Any]:
        """Remove an API key by ID."""
        conn = self._db.get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id, name FROM api_keys WHERE id = ?", (key_id,))
        row = cursor.fetchone()
        if not row:
            return {"success": False, "error": f"Key {key_id} not found"}

        cursor.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
        conn.commit()

        logger.info(f"[KeyManager] Removed key {key_id} ({row['name']})")
        return {"success": True, "removed_id": key_id, "name": row["name"]}


# Module-level singleton accessor
def get_key_manager() -> KeyManager:
    """Get the singleton KeyManager instance (thread-safe)."""
    return KeyManager()


def reset_key_manager() -> None:
    """Reset the KeyManager singleton (for testing)."""
    with KeyManager._instance_lock:
        if KeyManager._instance is not None:
            if hasattr(KeyManager._instance, "_initialized"):
                delattr(KeyManager._instance, "_initialized")
        KeyManager._instance = None
    logger.debug("[KeyManager] Singleton instance reset")
