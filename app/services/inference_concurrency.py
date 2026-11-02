"""Event-loop-safe concurrency limits for shared inference services."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.core.config import settings


class LoopScopedSemaphore:
    """Keep one semaphore per event loop so tests and workers stay isolated."""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._semaphores: dict[int, asyncio.Semaphore] = {}

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        loop = asyncio.get_running_loop()
        key = id(loop)
        semaphore = self._semaphores.get(key)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._limit)
            self._semaphores[key] = semaphore
        await semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()


vlm_inference_gate = LoopScopedSemaphore(settings.production_vlm_parallelism)
