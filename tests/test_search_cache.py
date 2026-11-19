"""Short shared search cache contracts, independent of provider traffic."""

import math
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio.testing import ActivityEnvironment

from cpfc_trip.config import Settings
from cpfc_trip.domain import Party, SearchBatch, SearchSpec, SessionInput
from cpfc_trip.persistence.database import engine
from cpfc_trip.persistence.models import Base
from cpfc_trip.persistence.search_cache import STALE_GAP, SearchCache
from cpfc_trip.planner.recorded import sample
from cpfc_trip.temporal.activities import SearchActivities

CHECKED = datetime(2026, 10, 5, 12, tzinfo=UTC)


@pytest.fixture
async def cache_engine(settings: Settings, tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """Create a separate database so two cache instances share durable rows."""
    database = engine(
        settings.model_copy(update={"database_url": f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}"})
    )
    async with database.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


def search_spec(data: SessionInput) -> SearchSpec:
    """Build a dated request with a known origin, destination and party."""
    return SearchSpec(
        fixture=data.fixtures[0],
        route=data.routes[0],
        party=Party(adults=2),
        outbound_date=date(2026, 10, 14),
        return_date=date(2026, 10, 16),
    )


async def test_transport_cache_is_shared_for_24_hours_and_party_safe(
    cache_engine: AsyncEngine, session_input: SessionInput
) -> None:
    """Only the same route, dates, mode and travellers may reuse a journey."""
    request = search_spec(session_input)
    batch = sample(request, "flight").model_copy(update={"calls": 3})
    writer = SearchCache(cache_engine)
    reader = SearchCache(cache_engine)
    await writer.put(request, "flight", batch, now=CHECKED)

    hit = await reader.get(request, "flight", now=CHECKED + timedelta(hours=23))
    assert hit is not None and hit.calls == 0
    assert hit.journeys == batch.journeys
    assert await reader.get(request, "rail", now=CHECKED) is None
    assert (
        await reader.get(
            request.model_copy(update={"party": Party(adults=1)}), "flight", now=CHECKED
        )
        is None
    )
    assert (
        await reader.get(
            request.model_copy(update={"return_date": date(2026, 10, 17)}), "flight", now=CHECKED
        )
        is None
    )
    assert await reader.get(request, "flight", now=CHECKED + timedelta(hours=24)) is None


async def test_configured_ttl_applies_to_rows_already_stored(
    cache_engine: AsyncEngine, session_input: SessionInput
) -> None:
    """Changing the TTL changes reuse of existing rows; zero always searches live."""
    request = search_spec(session_input)
    batch = sample(request, "flight")
    await SearchCache(cache_engine).put(request, "flight", batch, now=CHECKED)
    later = CHECKED + timedelta(hours=30)

    assert await SearchCache(cache_engine).get(request, "flight", now=later) is None
    week = SearchCache(cache_engine, timedelta(days=7))
    assert await week.get(request, "flight", now=later) is not None
    short = SearchCache(cache_engine, timedelta(minutes=10))
    assert await short.get(request, "flight", now=CHECKED + timedelta(minutes=11)) is None

    disabled = SearchCache(cache_engine, timedelta(0))
    assert await disabled.get(request, "flight", now=CHECKED) is None
    other = request.model_copy(update={"party": Party(adults=1)})
    await disabled.put(other, "flight", sample(other, "flight"), now=CHECKED)
    assert await week.get(other, "flight", now=CHECKED) is None


async def test_early_expiration_scales_with_search_cost_and_draw(
    cache_engine: AsyncEngine, session_input: SessionInput
) -> None:
    """XFetch: a read may refresh before the TTL by fetch_seconds * beta * -ln(1 - U)."""
    request = search_spec(session_input)
    await SearchCache(cache_engine).put(
        request, "flight", sample(request, "flight"), fetch_seconds=10, now=CHECKED
    )
    nearly_expired = CHECKED + timedelta(hours=23, minutes=30)

    def cache(draw: float) -> SearchCache:
        return SearchCache(cache_engine, early_beta=360, uniform=lambda: draw)

    # A draw of 0 never refreshes early; 1 - 1/e refreshes one mean lead (10 s x 360 = 1 h) early.
    assert await cache(0.0).get(request, "flight", now=nearly_expired) is not None
    lead = 1 - math.exp(-1)
    assert await cache(lead).get(request, "flight", now=nearly_expired) is None
    assert await cache(lead).get(request, "flight", now=CHECKED + timedelta(hours=22)) is not None
    cheap = request.model_copy(update={"party": Party(adults=1)})
    await SearchCache(cache_engine).put(
        cheap, "flight", sample(cheap, "flight"), fetch_seconds=1, now=CHECKED
    )
    assert await cache(lead).get(cheap, "flight", now=nearly_expired) is not None


@pytest.mark.parametrize(("age_hours", "served"), [(30, True), (49, False)])
async def test_failed_live_search_falls_back_to_recent_cached_result(
    cache_engine: AsyncEngine,
    session_input: SessionInput,
    monkeypatch: pytest.MonkeyPatch,
    age_hours: int,
    served: bool,
) -> None:
    """Stale-if-error: within TTL + window a cached copy stands in, labelled as older."""
    request = search_spec(session_input)
    batch = sample(request, "flight")
    await SearchCache(cache_engine).put(
        request, "flight", batch, now=datetime.now(UTC) - timedelta(hours=age_hours)
    )
    failure = SearchBatch(gaps=("Provider failed",), calls=3, provider_failed=True)

    async def failing_acquire(spec: SearchSpec, mode: str) -> SearchBatch:
        return failure

    monkeypatch.setattr("cpfc_trip.temporal.activities.acquire", failing_acquire)
    cache = SearchCache(cache_engine, stale_if_error=timedelta(hours=24))
    result = await SearchActivities(cache).search_flights(request)
    if served:
        assert result.journeys == batch.journeys
        assert STALE_GAP in result.gaps
        assert result.calls == 3
    else:
        assert result == failure


async def test_empty_live_result_does_not_use_stale_copy(
    cache_engine: AsyncEngine, session_input: SessionInput, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only provider errors fall back; a genuine no-results answer is shown as it is."""
    request = search_spec(session_input)
    await SearchCache(cache_engine).put(
        request, "flight", sample(request, "flight"), now=datetime.now(UTC) - timedelta(hours=30)
    )
    empty = SearchBatch(gaps=("No flights",), calls=2)

    async def empty_acquire(spec: SearchSpec, mode: str) -> SearchBatch:
        return empty

    monkeypatch.setattr("cpfc_trip.temporal.activities.acquire", empty_acquire)
    cache = SearchCache(cache_engine, stale_if_error=timedelta(hours=24))
    assert await SearchActivities(cache).search_flights(request) == empty


async def test_hotel_cache_ignores_route_but_keys_preferences(
    cache_engine: AsyncEngine, session_input: SessionInput
) -> None:
    """Accommodation can be reused across routes, never across party or room needs."""
    request = search_spec(session_input)
    batch = sample(request, "stay").model_copy(update={"calls": 3})
    cache = SearchCache(cache_engine)
    await cache.put(request, "stay", batch, now=CHECKED)

    other_route = request.model_copy(update={"route": session_input.routes[1]})
    hit = await cache.get(other_route, "stay", now=CHECKED)
    assert hit is not None and hit.calls == 0 and hit.stays == batch.stays
    assert (
        await cache.get(request.model_copy(update={"budget_tier": "comfort"}), "stay", now=CHECKED)
        is None
    )
    assert (
        await cache.get(request.model_copy(update={"private_bathroom": True}), "stay", now=CHECKED)
        is None
    )
    assert (
        await cache.get(request.model_copy(update={"party": Party(adults=1)}), "stay", now=CHECKED)
        is None
    )
    assert request.fixture.venue_coordinates is not None
    moved_venue = request.fixture.model_copy(
        update={
            "venue_coordinates": request.fixture.venue_coordinates.model_copy(
                update={"latitude": request.fixture.venue_coordinates.latitude + 0.01}
            )
        }
    )
    assert (
        await cache.get(request.model_copy(update={"fixture": moved_venue}), "stay", now=CHECKED)
        is None
    )
    assert await cache.get(request, "stay", now=CHECKED + timedelta(hours=24)) is None


async def test_incomplete_results_are_not_shared(
    cache_engine: AsyncEngine, session_input: SessionInput
) -> None:
    """Provider failures and no-result searches must be retried on the next request."""
    cache = SearchCache(cache_engine)
    request = search_spec(session_input)
    await cache.put(request, "flight", SearchBatch(gaps=("Provider failed",), calls=1), now=CHECKED)
    assert await cache.get(request, "flight", now=CHECKED) is None


async def test_activity_uses_shared_cache_without_charging_calls(
    cache_engine: AsyncEngine, session_input: SessionInput, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second worker uses the first worker's result and original quote times."""
    request = search_spec(session_input)
    batch = sample(request, "flight").model_copy(update={"calls": 2})
    calls = 0

    async def fake_acquire(spec: SearchSpec, mode: str) -> SearchBatch:
        nonlocal calls
        assert spec == request and mode == "flight"
        calls += 1
        return batch

    monkeypatch.setattr("cpfc_trip.temporal.activities.acquire", fake_acquire)
    first = SearchActivities(SearchCache(cache_engine))
    second = SearchActivities(SearchCache(cache_engine))
    assert await first.search_flights(request) == batch
    reused = await second.search_flights(request)
    assert reused.calls == 0
    assert reused.journeys == batch.journeys
    assert calls == 1


async def test_cache_failure_falls_through_to_provider(
    cache_engine: AsyncEngine, session_input: SessionInput, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache outage must not prevent a paid live search from proceeding."""
    request = search_spec(session_input)
    batch = sample(request, "flight")
    provider_calls = 0

    async def broken_get(*args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("cache unavailable")

    async def fake_acquire(spec: SearchSpec, mode: str) -> SearchBatch:
        nonlocal provider_calls
        provider_calls += 1
        return batch

    monkeypatch.setattr(SearchCache, "get", broken_get)
    monkeypatch.setattr("cpfc_trip.temporal.activities.acquire", fake_acquire)
    assert await SearchActivities(SearchCache(cache_engine)).search_flights(request) == batch
    assert provider_calls == 1


async def test_recovered_search_reserves_both_attempts(
    cache_engine: AsyncEngine, session_input: SessionInput, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ambiguous first attempt cannot refund its possibly paid calls."""
    request = search_spec(session_input)
    observed_heartbeats: list[tuple[object, ...]] = []

    async def fake_acquire(spec: SearchSpec, mode: str) -> SearchBatch:
        return sample(spec, mode).model_copy(update={"calls": 3})

    monkeypatch.setattr("cpfc_trip.temporal.activities.acquire", fake_acquire)
    environment = ActivityEnvironment()
    environment.info = replace(environment.info, attempt=2)
    environment.on_heartbeat = lambda *details: observed_heartbeats.append(details)
    recovered = await environment.run(
        SearchActivities(SearchCache(cache_engine)).search_flights, request
    )
    assert recovered.journeys
    assert recovered.calls == 16
    assert observed_heartbeats
