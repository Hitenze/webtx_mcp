"""
Tests for webtx-mcp API Key Manager.
"""

import os
import pytest
from datetime import datetime, timedelta
from pathlib import Path


@pytest.fixture(autouse=True)
def setup_and_teardown(tmp_path):
    """Setup and teardown for each test using pytest tmp_path."""
    import webtx_mcp.db as db_module
    import webtx_mcp.key_manager as key_manager

    # Reset singletons
    db_module.reset_db()
    key_manager.KeyManager._instance = None

    # Use tmp_path for test database
    test_db_path = tmp_path / "test.db"
    os.environ["WEBTX_MCP_DB_PATH"] = str(test_db_path)

    # Use tmp_path for master key
    test_master_key_path = tmp_path / ".master_key"
    key_manager._MASTER_KEY_PATH = test_master_key_path

    yield

    # Cleanup
    db_module.reset_db()
    key_manager.KeyManager._instance = None
    key_manager._MASTER_KEY_PATH = Path.home() / ".webtx_mcp" / ".master_key"

    if "WEBTX_MCP_DB_PATH" in os.environ:
        del os.environ["WEBTX_MCP_DB_PATH"]


class TestBasic:
    """Basic tests for KeyManager functionality."""

    def test_add_key(self):
        """Test adding a new API key."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()
        result = manager.add_key(
            key="test_key_123",
            name="Test Key",
            monthly_limit=100,
        )

        assert result["success"] is True
        assert "key_id" in result
        assert result["key_id"] > 0

    def test_add_duplicate_key(self):
        """Test that duplicate keys are rejected."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        result1 = manager.add_key(key="same_key")
        assert result1["success"] is True

        result2 = manager.add_key(key="same_key")
        assert result2["success"] is False
        assert "already exists" in result2["error"].lower()

    def test_add_key_empty(self):
        """Test that empty key is rejected."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()
        result = manager.add_key(key="")

        assert result["success"] is False
        assert "empty" in result["error"].lower()

    def test_add_key_whitespace_only(self):
        """Test that whitespace-only key is rejected."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()
        result = manager.add_key(key="   ")

        assert result["success"] is False
        assert "empty" in result["error"].lower()

    def test_add_key_negative_monthly_limit(self):
        """Test that negative monthly_limit is rejected."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()
        result = manager.add_key(key="test_key", monthly_limit=-1)

        assert result["success"] is False
        assert "negative" in result["error"].lower()

    def test_add_key_negative_daily_limit(self):
        """Test that negative daily_limit is rejected."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()
        result = manager.add_key(key="test_key", daily_limit=-1)

        assert result["success"] is False
        assert "negative" in result["error"].lower()

    def test_list_keys(self):
        """Test listing keys."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        manager.add_key(key="key1", name="Key 1")
        manager.add_key(key="key2", name="Key 2")

        keys = manager.list_keys()
        assert len(keys) == 2

    def test_remove_key(self):
        """Test removing a key."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        result = manager.add_key(key="to_remove", name="Remove Me")
        key_id = result["key_id"]

        remove_result = manager.remove_key(key_id)
        assert remove_result["success"] is True

        keys = manager.list_keys()
        assert len(keys) == 0

    def test_remove_nonexistent_key(self):
        """Test removing a key that doesn't exist."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()
        result = manager.remove_key(99999)

        assert result["success"] is False
        assert "not found" in result["error"]


class TestKeySelection:
    """Tests for key selection and load balancing."""

    def test_get_key_usage_ratio_balancing(self):
        """Test that keys are selected based on usage ratio."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        r1 = manager.add_key(key="key1", name="Key 1", monthly_limit=100)
        r2 = manager.add_key(key="key2", name="Key 2", monthly_limit=100)

        conn = manager._db.get_connection()
        cursor = conn.cursor()

        # Set key1 usage to 80 (80% used)
        cursor.execute(
            "UPDATE api_keys SET usage_count = 80 WHERE id = ?",
            (r1["key_id"],),
        )
        # Set key2 usage to 20 (20% used)
        cursor.execute(
            "UPDATE api_keys SET usage_count = 20 WHERE id = ?",
            (r2["key_id"],),
        )
        conn.commit()

        # Get key - should select key2 (lower usage ratio)
        selected = manager.get_key()
        assert selected is not None
        assert selected.key == "key2"

    def test_suspended_key_excluded(self):
        """Test that suspended keys are not selected."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        manager.add_key(key="key1", name="Key 1")
        result2 = manager.add_key(key="key2", name="Key 2")

        conn = manager._db.get_connection()
        cursor = conn.cursor()
        future = (datetime.now() + timedelta(hours=1)).isoformat()
        cursor.execute(
            "UPDATE api_keys SET status = 'suspended', suspended_until = ? WHERE id = ?",
            (future, result2["key_id"]),
        )
        conn.commit()

        selected = manager.get_key()
        assert selected is not None
        assert selected.key == "key1"

    def test_disabled_key_excluded(self):
        """Test that disabled keys are never selected."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        result = manager.add_key(key="key1", name="Key 1")
        key_id = result["key_id"]

        conn = manager._db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE api_keys SET status = 'disabled' WHERE id = ?", (key_id,)
        )
        conn.commit()

        selected = manager.get_key()
        # Will fall back to env if GOOGLE_API_KEY is set, otherwise None
        if selected:
            assert selected.id == -1  # Env fallback

    @pytest.fixture(autouse=False)
    def env_key(self):
        os.environ["GOOGLE_API_KEY"] = "env_fallback_key"
        yield
        if "GOOGLE_API_KEY" in os.environ:
            del os.environ["GOOGLE_API_KEY"]

    def test_env_fallback(self, env_key):
        """Test fallback to .env when no DB keys available."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        selected = manager.get_key()
        assert selected is not None
        assert selected.id == -1
        assert selected.key == "env_fallback_key"
        assert ".env" in selected.name


class TestErrorHandling:
    """Tests for error handling and circuit breaker."""

    def test_report_success(self):
        """Test that reporting success updates counts."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        result = manager.add_key(key="key1")
        key_id = result["key_id"]

        manager.report_success(key_id)
        manager.report_success(key_id)
        manager.report_success(key_id)

        keys = manager.list_keys()
        assert keys[0]["usage_count"] == 3
        assert keys[0]["daily_usage"] == 3
        assert keys[0]["total_usage"] == 3

    def test_report_failure_suspends_on_429(self):
        """Test that 429 errors suspend the key."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        result = manager.add_key(key="key1")
        key_id = result["key_id"]

        manager.report_failure(key_id, 429)

        keys = manager.list_keys()
        assert keys[0]["status"] == "suspended"
        assert keys[0]["suspended_until"] is not None

    def test_report_failure_disables_on_401(self):
        """Test that 401 errors disable the key permanently."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        result = manager.add_key(key="key1")
        key_id = result["key_id"]

        manager.report_failure(key_id, 401)

        keys = manager.list_keys()
        assert keys[0]["status"] == "disabled"

    def test_circuit_breaker(self):
        """Test circuit breaker suspends after consecutive failures."""
        from webtx_mcp.key_manager import get_key_manager, MAX_CONSECUTIVE_FAILURES

        manager = get_key_manager()

        result = manager.add_key(key="key1")
        key_id = result["key_id"]

        for _ in range(MAX_CONSECUTIVE_FAILURES):
            manager.report_failure(key_id, 500)

        keys = manager.list_keys()
        assert keys[0]["status"] == "suspended"
        assert keys[0]["consecutive_failures"] == MAX_CONSECUTIVE_FAILURES

    def test_success_resets_failures(self):
        """Test that success resets consecutive failure count."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()

        result = manager.add_key(key="key1")
        key_id = result["key_id"]

        manager.report_failure(key_id, 500)
        manager.report_failure(key_id, 500)

        keys = manager.list_keys()
        assert keys[0]["consecutive_failures"] == 2

        manager.report_success(key_id)

        keys = manager.list_keys()
        assert keys[0]["consecutive_failures"] == 0


class TestEncryption:
    """Tests for API key encryption."""

    def test_encrypt_decrypt_roundtrip(self):
        """Test that encrypt/decrypt roundtrip preserves the key."""
        from webtx_mcp.key_manager import _encrypt_key, _decrypt_key

        original = "sk-test-api-key-12345"
        encrypted = _encrypt_key(original)
        decrypted = _decrypt_key(encrypted)

        assert decrypted == original
        assert encrypted != original
        assert encrypted.startswith("gAAAAA")

    def test_decrypt_plaintext_fallback(self):
        """Test backward compat: _decrypt_key returns plaintext if not encrypted."""
        from webtx_mcp.key_manager import _decrypt_key

        plaintext = "plain_api_key_value"
        result = _decrypt_key(plaintext)
        assert result == plaintext

    def test_add_key_stores_encrypted(self):
        """Test that add_key stores encrypted value in DB, not plaintext."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()
        original_key = "my_secret_api_key_xyz"

        result = manager.add_key(key=original_key, name="Enc Test")
        assert result["success"] is True

        conn = manager._db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT encrypted_key FROM api_keys WHERE id = ?",
            (result["key_id"],),
        )
        row = cursor.fetchone()
        stored_value = row[0]

        assert stored_value != original_key
        assert stored_value.startswith("gAAAAA")

    def test_get_key_returns_decrypted(self):
        """Test that get_key returns decrypted plaintext key."""
        from webtx_mcp.key_manager import get_key_manager

        manager = get_key_manager()
        original_key = "my_secret_api_key_abc"

        manager.add_key(key=original_key, name="Dec Test")

        selected = manager.get_key()
        assert selected is not None
        assert selected.key == original_key

    def test_master_key_auto_generated(self, tmp_path):
        """Test that master key is auto-generated on first use."""
        import webtx_mcp.key_manager as key_manager

        fresh_key_path = tmp_path / "subdir" / ".master_key"
        key_manager._MASTER_KEY_PATH = fresh_key_path

        assert not fresh_key_path.exists()

        from webtx_mcp.key_manager import _get_or_create_master_key

        master_key = _get_or_create_master_key()
        assert fresh_key_path.exists()
        assert len(master_key) > 0

        mode = fresh_key_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_master_key_reused(self, tmp_path):
        """Test that existing master key is reused, not regenerated."""
        import webtx_mcp.key_manager as key_manager

        fresh_key_path = tmp_path / ".master_key2"
        key_manager._MASTER_KEY_PATH = fresh_key_path

        from webtx_mcp.key_manager import _get_or_create_master_key

        key1 = _get_or_create_master_key()
        key2 = _get_or_create_master_key()
        assert key1 == key2

    def test_different_keys_produce_different_ciphertexts(self):
        """Test that encrypting different values produces different results."""
        from webtx_mcp.key_manager import _encrypt_key

        enc1 = _encrypt_key("key_alpha")
        enc2 = _encrypt_key("key_beta")
        assert enc1 != enc2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
