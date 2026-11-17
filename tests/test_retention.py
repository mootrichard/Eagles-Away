"""Planning sessions are kept for at most 30 days in the application database."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from cpfc_trip.persistence.models import SessionRow
from cpfc_trip.persistence.retention import prune_expired


async def test_prune_expired_removes_old_sessions(repository) -> None:
    """Retain recent plans and remove sessions outside the retention window."""
    now = datetime(2026, 10, 9, tzinfo=UTC)
    async with repository.sessions() as db:
        for sid, age in (("old", 31), ("recent", 29)):
            db.add(
                SessionRow(
                    id=sid,
                    submission_id=sid,
                    request_hash="r",
                    access_hash="a",
                    created_at=now - timedelta(days=age),
                    workflow_input={},
                )
            )
        await db.commit()

    assert await prune_expired(repository.sessions, now=now) == 1
    async with repository.sessions() as db:
        assert list(await db.scalars(select(SessionRow.id))) == ["recent"]
