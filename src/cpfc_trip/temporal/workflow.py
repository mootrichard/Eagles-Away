"""One bounded planning run; Queries read progress while the run commits each step."""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    # Pydantic imports this lazily during validation; load it before sandbox activation.
    import annotated_types as annotated_types
    from temporalio.common import RetryPolicy

    from cpfc_trip.domain import Itinerary, SearchBatch, SearchSpec, SessionInput, Snapshot, Trip
    from cpfc_trip.planner.planning import choose, enumerate_specs, feasible_journey, feasible_stay
    from cpfc_trip.planner.providers.searchapi import (
        REQUEST_CAPS,
        SEARCH_ACTIVITY_ATTEMPTS,
        fingerprint,
    )
    from cpfc_trip.planner.recorded import sample


class NoCompleteTrip(Exception):
    """A specific, safe explanation for a search with no verifiable travel."""


@workflow.defn(name="TravelPlanningSessionWorkflowV4")
class TravelPlanningSessionWorkflow:
    @workflow.init
    def __init__(self, data: SessionInput):
        self.data = data
        self.state = Snapshot(
            development_mode=data.planner_mode == "recorded",
            public_session_id=data.public_session_id,
            origin_city=data.brief.origin_city,
            progress_message=f"Checking routes from {data.brief.origin_city}…",
        )

    def change(self, **fields: Any) -> None:
        self.state = self.state.model_copy(
            update={**fields, "state_revision": self.state.state_revision + 1}
        )

    @workflow.query
    def get_snapshot(self, after_revision: int | None = None) -> Snapshot | None:
        if after_revision == self.state.state_revision:
            return None
        return self.state

    async def plan(self, deadline: datetime) -> Itinerary:
        brief = self.data.brief
        self.change(phase="researching", progress_message="Checking your travel preferences…")
        if brief.transport_mode == "rail" and not any(
            route.mode == "rail" for route in self.data.routes
        ):
            raise NoCompleteTrip("Train search is not available from this city.")
        specs = enumerate_specs(
            brief,
            self.data.fixtures,
            self.data.routes,
            recorded=self.data.planner_mode == "recorded",
        )
        used = 0
        batches: dict[str, list[SearchBatch]] = {f.id: [] for f in self.data.fixtures}
        flight_batches: list[SearchBatch] = []
        by_fixture = {f.id: [s for s in specs if s.fixture.id == f.id] for f in self.data.fixtures}
        waves = [
            [options[index] for options in by_fixture.values() if index < len(options)]
            for index in range(max(map(len, by_fixture.values()), default=0))
        ]
        seen: set[str] = set()
        self.change(progress_message="Comparing transport and accommodation…")
        semaphore = asyncio.Semaphore(4)
        first_journey_at: datetime | None = None

        def show_partial_trip() -> None:
            nonlocal first_journey_at
            if self.state.preview_trip is not None and self.state.preview_trip.journey:
                return
            for fixture in self.data.fixtures:
                for batch in batches[fixture.id]:
                    for journey in batch.journeys:
                        route = next(
                            (item for item in self.data.routes if item.id == journey.route_id),
                            None,
                        )
                        if route and feasible_journey(journey, fixture, brief, route):
                            first_journey_at = workflow.now()
                            self.change(
                                preview_trip=Trip(
                                    fixture=fixture,
                                    journey=journey,
                                    summary="A dated return journey was found. We’re still checking accommodation and other options.",
                                    gaps=(
                                        "Accommodation and local transfers are still being checked.",
                                    ),
                                ),
                                progress_message="A journey was found. Checking stays and alternatives…",
                            )
                            return
            if self.state.preview_trip is None:
                for fixture in self.data.fixtures:
                    for batch in batches[fixture.id]:
                        for stay in batch.stays:
                            if feasible_stay(stay, fixture, brief):
                                self.change(
                                    preview_trip=Trip(
                                        fixture=fixture,
                                        stay=stay,
                                        summary="A dated stay was found. We’re still checking return travel.",
                                        gaps=("Return travel is still being checked.",),
                                    ),
                                    progress_message="A stay was found. Checking return travel…",
                                )
                                return

        async def acquire_job(
            key: str, kind: str, spec: SearchSpec, cap: int
        ) -> tuple[str, str, str, SearchBatch, int, bool]:
            name = (
                "search_stays"
                if kind == "stay"
                else "search_flights"
                if kind == "flight"
                else "search_trains"
            )
            async with semaphore:
                try:
                    response = await workflow.execute_activity(
                        name,
                        spec,
                        result_type=SearchBatch,
                        start_to_close_timeout=timedelta(seconds=85),
                        schedule_to_close_timeout=timedelta(seconds=175),
                        heartbeat_timeout=timedelta(seconds=15),
                        retry_policy=RetryPolicy(maximum_attempts=SEARCH_ACTIVITY_ATTEMPTS),
                    )
                    known_calls = True
                except Exception:
                    response = SearchBatch(gaps=("This travel search could not be completed.",))
                    known_calls = False
            return key, kind, spec.fixture.id, response, cap, known_calls

        trips: tuple[Trip, ...] = ()
        for wave_index, wave in enumerate(waves):
            if (
                self.data.planner_mode != "recorded"
                and (deadline - workflow.now()).total_seconds() <= 2
            ):
                break
            jobs: list[tuple[str, str, SearchSpec, int]] = []
            for spec in wave:
                for kind in (spec.route.mode, "stay"):
                    if self.data.planner_mode == "recorded":
                        response = sample(spec, kind)
                        batches[spec.fixture.id].append(response)
                        if kind == "flight":
                            flight_batches.append(response)
                        continue
                    key = fingerprint(
                        (
                            spec.fixture.id,
                            kind,
                            spec.route.id if kind != "stay" else "",
                            str(spec.outbound_date),
                            str(spec.return_date),
                            spec.party.model_dump(mode="json"),
                            (spec.budget_tier, spec.private_room, spec.private_bathroom)
                            if kind == "stay"
                            else None,
                        )
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    cap = REQUEST_CAPS[kind] * SEARCH_ACTIVITY_ATTEMPTS
                    if used + cap > self.data.limits.search_calls:
                        continue
                    used += cap
                    jobs.append((key, kind, spec, cap))
            timed_out = False
            show_partial_trip()
            if jobs:
                tasks = [asyncio.create_task(acquire_job(*job)) for job in jobs]
                pending = tasks
                while pending:
                    search_end = deadline - timedelta(seconds=2)
                    if first_journey_at is not None:
                        search_end = min(search_end, first_journey_at + timedelta(seconds=30))
                    done, pending = await workflow.wait(
                        pending,
                        timeout=max(0, (search_end - workflow.now()).total_seconds()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        timed_out = True
                        break
                    for task in done:
                        key, kind, fixture_id, response, cap, known_calls = task.result()
                        if known_calls:
                            used -= cap - min(cap, response.calls)
                        batches[fixture_id].append(response)
                        if kind == "flight":
                            flight_batches.append(response)
                    show_partial_trip()
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            trips = tuple(
                choose(f, batches[f.id], brief, self.data.routes) for f in self.data.fixtures
            )
            if (
                timed_out
                or (wave_index >= 1 and all(t.journey and t.stay for t in trips))
                or (
                    first_journey_at is not None
                    and workflow.now() >= first_journey_at + timedelta(seconds=30)
                )
            ):
                break
        if not any(t.journey and t.stay for t in trips):
            if (
                specs
                and all(spec.route.mode == "flight" for spec in specs)
                and flight_batches
                and all(
                    not batch.journeys
                    and any(
                        "No nonstop or suitable one-stop flights were found" in gap
                        for gap in batch.gaps
                    )
                    for batch in flight_batches
                )
            ):
                onward = specs[0].route.onward_stations
                destination = onward[0] if onward else self.data.fixtures[0].city
                connection = (
                    f" A flight to {destination} is needed before the train to "
                    f"{self.data.fixtures[0].city}."
                    if onward
                    else ""
                )
                alternatives = (
                    "Try trains, different travel dates or another starting city."
                    if any(route.mode == "rail" for route in self.data.routes)
                    else "Try different travel dates or another starting city."
                )
                raise NoCompleteTrip(
                    f"No nonstop or suitable one-stop flights were found from {brief.origin_city} to "
                    f"{destination} on the dates searched.{connection} " + alternatives
                )
            if any(
                feasible_journey(journey, fixture, brief, route)
                for fixture in self.data.fixtures
                for batch in batches[fixture.id]
                for journey in batch.journeys
                for route in self.data.routes
                if route.id == journey.route_id
            ):
                raise NoCompleteTrip(
                    "We found a dated return journey, but couldn't verify a stay "
                    "that fits its dates and room requirements. Try different travel "
                    "dates or room preferences."
                )
            if any(batch.journeys for results in batches.values() for batch in results):
                raise NoCompleteTrip(
                    "We found return journeys, but none passed the requested date, "
                    "match timing and connection checks. Try different travel dates "
                    "or another starting city."
                )
            if specs:
                raise NoCompleteTrip(
                    "We couldn't find a complete trip within the search limits because "
                    "no dated return journey was verified in the results checked. Try "
                    "different travel dates, another starting city or another travel mode."
                )
            raise NoCompleteTrip(
                "No supported travel route was available for this match, city and "
                "travel mode. Try another starting city or travel mode."
            )
        return Itinerary(
            generated_at=workflow.now(),
            trips=trips,
            caveats=(
                "Prices may have changed since retrieval. Confirm the full price before booking.",
                f"Match tickets and travel to/from your {brief.origin_city} departure hub are not included.",
            ),
        )

    @workflow.run
    async def run(self, data: SessionInput) -> Snapshot:
        try:
            itinerary = await asyncio.wait_for(
                self.plan(workflow.now() + timedelta(seconds=data.limits.planning_seconds)),
                timeout=data.limits.planning_seconds,
            )
        except Exception as exc:
            # Deliberately omit raw provider errors; partial results stay visible.
            self.change(
                phase="failed",
                progress_message=str(exc)
                if isinstance(exc, NoCompleteTrip)
                else "We couldn't find a complete trip matching this request within the search "
                "limits. Try again or adjust your preferences.",
            )
            return self.state
        self.change(
            itinerary=itinerary,
            preview_trip=None,
            phase="closed",
            progress_message="Your trip is ready. Check the outstanding details before booking.",
        )
        return self.state
