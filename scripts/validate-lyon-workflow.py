"""Paid live Lyon validation: bounded SearchApi searches through local Temporal."""

import argparse
import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from uuid import uuid4

from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from cpfc_trip.catalog import load_catalog
from cpfc_trip.config import Settings
from cpfc_trip.domain import Brief, CreateSession
from cpfc_trip.persistence.database import engine
from cpfc_trip.persistence.repository import Repository
from cpfc_trip.persistence.search_cache import SearchCache
from cpfc_trip.temporal.activities import SearchActivities
from cpfc_trip.temporal.workflow import TravelPlanningSessionWorkflow as Workflow


async def main() -> None:
    settings = Settings(planner_mode="live")
    db = engine(settings)
    repo = Repository(db, settings)
    fixture = next(f for f in load_catalog()[0] if f.city == "Lyon")
    data = await repo.create(
        CreateSession(submission_id=uuid4(), brief=Brief(fixture_ids=(fixture.id,))),
        str(uuid4()),
    )
    search = SearchActivities(SearchCache(db))
    activities = [search.search_flights, search.search_stays, search.search_trains]
    queue = str(uuid4())
    folder = Path(".data/lyon-live-workflow")
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    try:
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=shutil.which("temporal"),
            data_converter=pydantic_data_converter,
        ) as env:
            async with Worker(
                env.client, task_queue=queue, workflows=[Workflow], activities=activities
            ):
                start = time.monotonic()
                handle = await env.client.start_workflow(
                    Workflow.run, data, id=str(data.public_session_id), task_queue=queue
                )
                try:
                    final = await handle.result()
                finally:
                    (folder / "history.json").write_text((await handle.fetch_history()).to_json())
                seconds = round(time.monotonic() - start, 2)
            assert final.phase == "closed", final.progress_message
            assert final.itinerary and final.itinerary.trips[0].journey
            print(f"Live plan: {seconds} seconds", flush=True)
            history = await handle.fetch_history()
            await Replayer(
                workflows=[Workflow], data_converter=pydantic_data_converter
            ).replay_workflow(history)
            (folder / "results.json").write_text(
                json.dumps(
                    {
                        "real_travel": True,
                        "final": final.model_dump(mode="json"),
                        "seconds": seconds,
                        "replay": "passed",
                    },
                    indent=2,
                )
            )
            print("Live plan and replay passed.")
    finally:
        await db.dispose()


async def replay_only() -> None:
    history = WorkflowHistory.from_json(
        "lyon-saved-validation",
        await asyncio.to_thread(Path(".data/lyon-live-workflow/history.json").read_text),
    )
    await Replayer(workflows=[Workflow], data_converter=pydantic_data_converter).replay_workflow(
        history
    )
    print("Saved live Lyon history replay passed; no search requests.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replay-only", action="store_true", help="Replay saved history without paid calls"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("cpfc_trip.model_usage").setLevel(logging.INFO)
    asyncio.run(replay_only() if args.replay_only else main())
