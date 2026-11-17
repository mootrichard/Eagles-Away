import asyncio
import logging
from contextlib import suppress

from sqlalchemy.exc import SQLAlchemyError
from temporalio import activity

from cpfc_trip.domain import SearchBatch, SearchSpec
from cpfc_trip.persistence.search_cache import SearchCache, SearchMode
from cpfc_trip.planner.providers.searchapi import REQUEST_CAPS, SEARCH_ACTIVITY_ATTEMPTS, acquire


class SearchActivities:
    """Search with a shared, best-effort cache before contacting paid providers."""

    def __init__(self, cache: SearchCache) -> None:
        self.cache = cache

    async def _search(self, spec: SearchSpec, mode: SearchMode) -> SearchBatch:
        heartbeat_task: asyncio.Task[None] | None = None
        if activity.in_activity():
            activity.heartbeat()
            heartbeat_task = asyncio.create_task(self._heartbeat())
        try:
            result = await self._cached_or_live(spec, mode)
            # The prior worker may have used every reserved provider call before loss.
            # A second attempt reports the worst case so the Workflow never refunds it.
            if activity.in_activity() and activity.info().attempt > 1:
                return result.model_copy(
                    update={"calls": REQUEST_CAPS[mode] * SEARCH_ACTIVITY_ATTEMPTS}
                )
            return result
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task

    @staticmethod
    async def _heartbeat() -> None:
        while True:
            await asyncio.sleep(5)
            activity.heartbeat()

    async def _cached_or_live(self, spec: SearchSpec, mode: SearchMode) -> SearchBatch:
        try:
            async with asyncio.timeout(1):
                cached = await self.cache.get(spec, mode)
            if cached is not None:
                return cached
        except (SQLAlchemyError, TimeoutError):
            logging.warning("Shared search cache read failed", exc_info=True)
        result = await acquire(spec, mode)
        try:
            async with asyncio.timeout(1):
                await self.cache.put(spec, mode, result)
        except (SQLAlchemyError, TimeoutError):
            logging.warning("Shared search cache write failed", exc_info=True)
        return result

    @activity.defn(name="search_flights")
    async def search_flights(self, spec: SearchSpec) -> SearchBatch:
        return await self._search(spec, "flight")

    @activity.defn(name="search_trains")
    async def search_trains(self, spec: SearchSpec) -> SearchBatch:
        return await self._search(spec, "rail")

    @activity.defn(name="search_stays")
    async def search_stays(self, spec: SearchSpec) -> SearchBatch:
        return await self._search(spec, "stay")
