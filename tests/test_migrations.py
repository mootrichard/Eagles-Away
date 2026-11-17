"""Retiring delivery storage must preserve saved plans and their access tokens."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_remove_email_storage_preserves_existing_session(tmp_path: Path) -> None:
    """Upgrade a legacy database without dropping its planning data."""
    database = tmp_path / "migration.db"
    environment = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20261009_0003"],
        env=environment,
        check=True,
        capture_output=True,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO sessions (id, submission_id, request_hash, access_hash, "
            "email_hash, encrypted_email, created_at, workflow_input, saved_itinerary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "saved",
                "submission",
                "request",
                "token-hash",
                "recipient-hash",
                b"recipient",
                "2026-11-17",
                "{}",
                '{"revision": 1}',
            ),
        )
        connection.execute(
            "INSERT INTO email_deliveries (session_id, payload_hash, payload, created_at, status, attempts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("saved", "payload-hash", "{}", "2026-11-17", "pending", 1),
        )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=environment,
        check=True,
        capture_output=True,
    )
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "email_deliveries" not in tables
        assert "email_webhooks" not in tables
        columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
        assert "email_hash" not in columns and "encrypted_email" not in columns
        assert connection.execute(
            "SELECT access_hash, saved_itinerary FROM sessions WHERE id = 'saved'"
        ).fetchone() == ("token-hash", '{"revision": 1}')
