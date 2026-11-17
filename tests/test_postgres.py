"""Run against an already migrated local database with TEST_DATABASE_URL set."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, text

from cpfc_trip.domain import CreateSession
from cpfc_trip.persistence.database import engine
from cpfc_trip.persistence.models import SessionRow
from cpfc_trip.persistence.repository import Repository


@pytest.fixture
async def pg(settings):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL locking checks")
    config = settings.model_copy(update={"database_url": url})
    db = engine(config)
    repo = Repository(db, config)
    async with db.connect() as conn:
        assert await conn.scalar(text("select version_num from alembic_version"))
    yield repo
    await db.dispose()


async def cleanup(repo, sid):
    async with repo.sessions() as db, db.begin():
        await db.execute(delete(SessionRow).where(SessionRow.id == str(sid)))


async def test_postgres_concurrent_submission_returns_one_session(pg, session_input):
    request = CreateSession(submission_id=uuid4(), brief=session_input.brief)
    results = await asyncio.gather(*(pg.create(request, "x" * 64) for _ in range(8)))
    sid = results[0].public_session_id
    try:
        assert all(x.public_session_id == sid for x in results)
        assert await pg.authorize(sid, "x" * 64)
        with pytest.raises(PermissionError):
            await pg.create(request, "y" * 64)
    finally:
        await cleanup(pg, sid)


async def test_postgres_reconnects_after_idle_connection_is_dropped(pg) -> None:
    """A server-closed idle connection is replaced before the next query."""
    async with pg.sessions() as session:
        backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
    killer = engine(pg.settings)
    try:
        async with killer.connect() as connection:
            assert await connection.scalar(
                text("SELECT pg_terminate_backend(:pid)"), {"pid": backend_pid}
            )
        async with pg.sessions() as session:
            assert await session.scalar(text("SELECT 1")) == 1
            assert await session.scalar(text("SELECT pg_backend_pid()")) != backend_pid
    finally:
        await killer.dispose()
