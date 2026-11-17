"""Alembic head must produce exactly the tables and columns the ORM models use."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from cpfc_trip.persistence.models import Base


def test_migrations_match_models(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"},
        check=True,
        capture_output=True,
    )
    with sqlite3.connect(database) as connection:
        migrated = {
            table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for (table,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'alembic_version'"
            )
        }
    assert migrated == {
        name: {column.name for column in table.columns}
        for name, table in Base.metadata.tables.items()
    }
