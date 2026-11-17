"""Session creation idempotency and completed-result reads."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.client import WorkflowExecutionStatus, WorkflowQueryFailedError

from cpfc_trip.api import create_app
from cpfc_trip.config import Settings
from cpfc_trip.domain import CreateSession, SessionInput
from cpfc_trip.persistence.repository import Repository


async def test_submission_retry_preserves_authorization(
    repository: Repository, session_input: SessionInput
) -> None:
    """A retried submission returns the same session; only its token is authorized."""
    request = CreateSession(submission_id=uuid4(), brief=session_input.brief)
    first = await repository.create(request, "x" * 64)
    assert await repository.create(request, "x" * 64) == first
    assert await repository.authorize(first.public_session_id, "x" * 64)
    assert not await repository.authorize(first.public_session_id, "y" * 64)
    with pytest.raises(PermissionError):
        await repository.create(request, "y" * 64)


async def test_api_creates_a_session_with_an_http_only_cookie(
    settings: Settings, session_input: SessionInput
) -> None:
    repository = AsyncMock()
    repository.create.return_value = session_input
    temporal = AsyncMock()
    app = create_app(settings, repository, temporal)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        response = await client.post(
            "/api/sessions",
            json={
                "submission_id": str(uuid4()),
                "brief": session_input.brief.model_dump(mode="json"),
            },
            headers={"Origin": settings.frontend_origin, "X-Submission-Token": "x" * 64},
        )
    assert response.status_code == 202
    assert "HttpOnly" in response.headers["set-cookie"]


async def test_completed_plan_survives_worker_retirement(
    settings: Settings, session_input: SessionInput
) -> None:
    """Read a completed result from Temporal when its pinned worker build is gone."""
    repository = AsyncMock()
    repository.authorize.return_value = True
    temporal = AsyncMock()
    handle = Mock()
    handle.query = AsyncMock(side_effect=WorkflowQueryFailedError("Old worker retired"))
    handle.describe = AsyncMock(return_value=Mock(status=WorkflowExecutionStatus.COMPLETED))
    handle.result = AsyncMock(
        return_value={
            "public_session_id": str(session_input.public_session_id),
            "state_revision": 7,
            "phase": "closed",
        }
    )
    temporal.get_workflow_handle = Mock(return_value=handle)
    app = create_app(settings, repository, temporal)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        client.cookies.set(f"cpfc_{session_input.public_session_id}", "x" * 64)
        path = f"/api/sessions/{session_input.public_session_id}/snapshot"
        response = await client.get(path)
        assert response.status_code == 200
        assert response.json()["phase"] == "closed"
        assert (await client.get(f"{path}?after_revision=7")).status_code == 204
