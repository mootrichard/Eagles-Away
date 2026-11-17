import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from cpfc_trip.domain import Limits, SearchBatch, SearchSpec
from cpfc_trip.planner.recorded import sample
from cpfc_trip.temporal.workflow import TravelPlanningSessionWorkflow as Workflow

TEMPLATE_SUMMARY = (
    "Balanced price and journey time. Transfer fares and outstanding checks are excluded."
)


@pytest.fixture
async def environment():
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal"),
        data_converter=pydantic_data_converter,
    ) as env:
        yield env


def live(session_input, **fields):
    return session_input.model_copy(
        update={
            "planner_mode": "live",
            "routes": (session_input.routes[0].model_copy(update={"enabled": True}),),
            **fields,
        }
    )


@activity.defn(name="search_flights")
async def sample_flights(spec: SearchSpec) -> SearchBatch:
    return sample(spec, "flight")


@activity.defn(name="search_stays")
async def sample_stays(spec: SearchSpec) -> SearchBatch:
    return sample(spec, "stay")


async def replay(handle) -> None:
    await Replayer(workflows=[Workflow], data_converter=pydantic_data_converter).replay_workflow(
        await handle.fetch_history()
    )


async def test_recorded_plan_query_restart_and_replay(environment, session_input):
    queue = str(uuid4())
    async with Worker(environment.client, task_queue=queue, workflows=[Workflow]):
        handle = await environment.client.start_workflow(
            Workflow.run, session_input, id=str(uuid4()), task_queue=queue
        )
        result = await handle.result()
        assert result.phase == "closed"
        assert result.development_mode
        assert result.itinerary and result.itinerary.trips[0].journey
        assert result.itinerary.trips[0].stay
        assert result.itinerary.trips[0].summary == TEMPLATE_SUMMARY
        assert result.preview_trip is None
        for _ in range(3):
            assert await handle.query(Workflow.get_snapshot, result.state_revision) is None
    # No worker-side in-memory state survives this fresh Worker instance.
    async with Worker(environment.client, task_queue=queue, workflows=[Workflow]):
        recovered = await handle.query(Workflow.get_snapshot)
    assert recovered == result
    await replay(handle)
    assert len(result.model_dump_json().encode()) < 100_000


async def test_no_route_fails_and_replays(environment, session_input):
    value = session_input.model_copy(update={"routes": ()})
    queue = str(uuid4())
    async with Worker(environment.client, task_queue=queue, workflows=[Workflow]):
        handle = await environment.client.start_workflow(
            Workflow.run, value, id=str(uuid4()), task_queue=queue
        )
        result = await handle.result()
    assert result.phase == "failed"
    assert result.itinerary is None
    assert "No supported travel route" in result.progress_message
    await replay(handle)


@pytest.mark.parametrize(
    ("origin", "fixture_id", "destination", "suggest_trains", "mixed_failure"),
    [
        ("Edinburgh", "uel-2026-lyon-away", "Lyon", True, False),
        ("Nottingham", "uel-2026-jagiellonia-away", "Warsaw", False, False),
        ("Nottingham", "uel-2026-jagiellonia-away", "Warsaw", False, True),
    ],
)
async def test_no_nonstop_flights_explains_failed_city_search(
    environment,
    session_input,
    origin,
    fixture_id,
    destination,
    suggest_trains,
    mixed_failure,
):
    from cpfc_trip.catalog import load_catalog, validate_brief
    from cpfc_trip.origins import routes_for_origin

    flight_calls = 0

    @activity.defn(name="search_flights")
    async def flights(spec: SearchSpec) -> SearchBatch:
        nonlocal flight_calls
        flight_calls += 1
        if mixed_failure and flight_calls == 2:
            return SearchBatch(
                gaps=("No complete flight and Warsaw–Białystok train connection was verified.",)
            )
        return SearchBatch(
            gaps=(
                "No nonstop or suitable one-stop flights were found for this route and date pair.",
            )
        )

    fixtures, catalog_routes = load_catalog()
    fixture = next(item for item in fixtures if item.id == fixture_id)
    brief = session_input.brief.model_copy(
        update={
            "origin_city": origin,
            "transport_mode": "flight",
            "fixture_ids": (fixture_id,),
            "windows": (),
        }
    )
    brief = validate_brief(brief, (fixture,), datetime(2026, 9, 6, tzinfo=UTC))
    routes = routes_for_origin(
        tuple(route for route in catalog_routes if route.fixture_id == fixture_id), origin
    )
    value = session_input.model_copy(
        update={"brief": brief, "fixtures": (fixture,), "routes": routes, "planner_mode": "live"}
    )
    queue = str(uuid4())
    async with Worker(
        environment.client,
        task_queue=queue,
        workflows=[Workflow],
        activities=[flights, sample_stays],
    ):
        result = await environment.client.execute_workflow(
            Workflow.run, value, id=str(uuid4()), task_queue=queue
        )
    assert result.phase == "failed"
    message = result.progress_message
    if mixed_failure:
        assert "No nonstop or suitable one-stop flights" not in message
        assert "couldn't find a complete trip" in message
        return
    assert (
        f"No nonstop or suitable one-stop flights were found from {origin} to {destination}"
        in message
    )
    assert ("Try trains" in message) is suggest_trains
    if destination == "Warsaw":
        assert "train to Białystok" in message


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("no_stay", "couldn't verify a stay"),
        ("late_return", "match timing and connection checks"),
        ("no_route", "no dated return journey was verified"),
        ("hotel_error", "couldn't verify a stay"),
        ("flight_error", "no dated return journey was verified"),
    ],
)
async def test_no_complete_trip_explains_evidence_gap(environment, session_input, reason, expected):
    @activity.defn(name="search_flights")
    async def flights(spec: SearchSpec) -> SearchBatch:
        if reason == "flight_error":
            raise ApplicationError("Travel provider unavailable", non_retryable=True)
        if reason == "no_route":
            return SearchBatch(gaps=("Complete outbound and return flights were not available.",))
        batch = sample(spec, "flight")
        if reason == "late_return":
            journey = batch.journeys[0]
            inbound = journey.inbound[0].model_copy(
                update={
                    "departs_at": spec.fixture.kickoff_at,
                    "arrives_at": spec.fixture.kickoff_at + timedelta(hours=2),
                }
            )
            return SearchBatch(journeys=(journey.model_copy(update={"inbound": (inbound,)}),))
        return batch

    @activity.defn(name="search_stays")
    async def stays(spec: SearchSpec) -> SearchBatch:
        if reason == "hotel_error":
            raise ApplicationError("Hotel provider unavailable", non_retryable=True)
        return SearchBatch() if reason == "no_stay" else sample(spec, "stay")

    queue = str(uuid4())
    async with Worker(
        environment.client,
        task_queue=queue,
        workflows=[Workflow],
        activities=[flights, stays],
    ):
        handle = await environment.client.start_workflow(
            Workflow.run, live(session_input), id=str(uuid4()), task_queue=queue
        )
        result = await handle.result()
    assert result.phase == "failed"
    assert result.itinerary is None
    assert expected in result.progress_message
    assert "unavailable" not in result.progress_message
    if reason in {"no_stay", "hotel_error"}:
        assert result.preview_trip and result.preview_trip.journey
        assert result.preview_trip.stay is None
    elif reason in {"no_route", "flight_error"}:
        assert result.preview_trip and result.preview_trip.stay
        assert result.preview_trip.journey is None
    await replay(handle)


async def test_live_plan_closes_with_template_summary_and_replays(environment, session_input):
    searches = []

    @activity.defn(name="search_flights")
    async def flights(spec: SearchSpec) -> SearchBatch:
        searches.append("flight")
        return sample(spec, "flight")

    @activity.defn(name="search_stays")
    async def stays(spec: SearchSpec) -> SearchBatch:
        searches.append("stay")
        return sample(spec, "stay")

    queue = str(uuid4())
    async with Worker(
        environment.client,
        task_queue=queue,
        workflows=[Workflow],
        activities=[flights, stays],
    ):
        handle = await environment.client.start_workflow(
            Workflow.run, live(session_input), id=str(uuid4()), task_queue=queue
        )
        result = await handle.result()
    assert result.phase == "closed"
    assert not result.development_mode
    assert result.preview_trip is None
    assert result.itinerary and len(result.itinerary.trips) == 1
    trip = result.itinerary.trips[0]
    assert trip.journey and trip.stay
    assert trip.summary == TEMPLATE_SUMMARY
    assert result.progress_message.startswith("Your trip is ready.")
    count = len(searches)
    assert count == 4  # Two waves give one verified option and one comparison.
    await replay(handle)
    assert len(searches) == count  # Replay uses the completed search Activity results.


async def test_live_search_retries_once_after_lost_attempt(environment, session_input):
    """A retryable lost attempt is recovered within the same planning run."""
    attempts: list[int] = []

    @activity.defn(name="search_flights")
    async def flights(spec: SearchSpec) -> SearchBatch:
        attempt = activity.info().attempt
        attempts.append(attempt)
        if attempt == 1 and len(attempts) == 1:
            raise RuntimeError("simulated worker loss")
        return sample(spec, "flight").model_copy(update={"calls": 16 if attempt > 1 else 0})

    queue = str(uuid4())
    async with Worker(
        environment.client,
        task_queue=queue,
        workflows=[Workflow],
        activities=[flights, sample_stays],
    ):
        result = await environment.client.execute_workflow(
            Workflow.run, live(session_input), id=str(uuid4()), task_queue=queue
        )
    assert result.phase == "closed"
    assert result.itinerary is not None
    assert attempts[:2] == [1, 2]


async def test_second_worker_recovers_a_search_when_first_worker_stops(environment, session_input):
    """A lost in-flight Activity can resume on another poller without losing the plan."""
    first_started = asyncio.Event()
    recovered = asyncio.Event()

    @activity.defn(name="search_flights")
    async def interrupted_flight(spec: SearchSpec) -> SearchBatch:
        activity.heartbeat()
        first_started.set()
        while True:
            await asyncio.sleep(1)
            activity.heartbeat()

    @activity.defn(name="search_flights")
    async def recovered_flight(spec: SearchSpec) -> SearchBatch:
        assert activity.info().attempt == 2
        recovered.set()
        return sample(spec, "flight").model_copy(update={"calls": 16})

    value = live(session_input, limits=Limits(planning_seconds=60))
    queue = str(uuid4())
    async with Worker(
        environment.client,
        task_queue=queue,
        workflows=[Workflow],
        activities=[interrupted_flight, sample_stays],
        graceful_shutdown_timeout=timedelta(0),
    ) as first_worker:
        handle = await environment.client.start_workflow(
            Workflow.run, value, id=str(uuid4()), task_queue=queue
        )
        await asyncio.wait_for(first_started.wait(), timeout=5)
        await first_worker.shutdown()
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[Workflow],
            activities=[recovered_flight, sample_stays],
        ):
            await asyncio.wait_for(recovered.wait(), timeout=25)
            result = await asyncio.wait_for(handle.result(), timeout=60)
    assert result.phase == "closed"
    assert result.itinerary and result.itinerary.trips[0].journey


async def test_slow_comparison_keeps_first_complete_wave(environment, session_input):
    from cpfc_trip.planner.planning import enumerate_specs

    value = live(session_input, limits=Limits(planning_seconds=5))
    first_spec = enumerate_specs(value.brief, value.fixtures, value.routes)[0]
    first_dates = (first_spec.outbound_date, first_spec.return_date)

    @activity.defn(name="search_flights")
    async def flights(spec: SearchSpec) -> SearchBatch:
        if (spec.outbound_date, spec.return_date) != first_dates:
            await asyncio.sleep(6)
        return sample(spec, "flight")

    @activity.defn(name="search_stays")
    async def stays(spec: SearchSpec) -> SearchBatch:
        if (spec.outbound_date, spec.return_date) != first_dates:
            await asyncio.sleep(6)
        return sample(spec, "stay")

    queue = str(uuid4())
    async with Worker(
        environment.client,
        task_queue=queue,
        workflows=[Workflow],
        activities=[flights, stays],
    ):
        result = await environment.client.execute_workflow(
            Workflow.run, value, id=str(uuid4()), task_queue=queue
        )
    assert result.phase == "closed"
    assert result.itinerary and result.itinerary.trips[0].journey
    assert result.itinerary.trips[0].stay
    assert result.itinerary.trips[0].stay.check_in == first_dates[0]


@pytest.mark.parametrize("first_kind", ["flight", "stay"])
async def test_partial_result_is_visible_while_other_search_continues(
    environment, session_input, first_kind
):
    search_started = asyncio.Event()
    release_search = asyncio.Event()

    @activity.defn(name="search_flights")
    async def flights(spec: SearchSpec) -> SearchBatch:
        if first_kind == "stay":
            search_started.set()
            await release_search.wait()
        return sample(spec, "flight")

    @activity.defn(name="search_stays")
    async def stays(spec: SearchSpec) -> SearchBatch:
        if first_kind == "flight":
            search_started.set()
            await release_search.wait()
        return sample(spec, "stay")

    queue = str(uuid4())
    async with Worker(
        environment.client,
        task_queue=queue,
        workflows=[Workflow],
        activities=[flights, stays],
    ):
        handle = await environment.client.start_workflow(
            Workflow.run, live(session_input), id=str(uuid4()), task_queue=queue
        )
        await asyncio.wait_for(search_started.wait(), timeout=5)
        for _ in range(50):
            state = await handle.query(Workflow.get_snapshot)
            if state.preview_trip:
                break
            await asyncio.sleep(0.05)
        assert state.phase == "researching"
        assert state.itinerary is None
        assert state.preview_trip is not None
        assert (state.preview_trip.journey is not None) == (first_kind == "flight")
        assert (state.preview_trip.stay is not None) == (first_kind == "stay")
        release_search.set()
        completed = await handle.result()
    assert completed.phase == "closed"
    assert completed.itinerary and completed.itinerary.trips[0].stay
    assert completed.preview_trip is None
