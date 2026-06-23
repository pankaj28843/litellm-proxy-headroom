from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Protocol

from .commands import CompressionActivityIngestCommand

logger = logging.getLogger(__name__)


class CompressionActivitySink(Protocol):
    async def post_compression_activity(
        self, command: CompressionActivityIngestCommand
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class AsyncIngestionBufferConfig:
    max_queue_size: int = 1000
    worker_count: int = 2
    max_attempts: int = 3
    retry_base_seconds: float = 0.1
    retry_max_seconds: float = 1.0
    shutdown_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.max_queue_size < 1:
            raise ValueError("max_queue_size must be greater than zero")
        if self.worker_count < 1:
            raise ValueError("worker_count must be greater than zero")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")
        if self.retry_base_seconds < 0:
            raise ValueError("retry_base_seconds must be greater than or equal to zero")
        if self.retry_max_seconds < 0:
            raise ValueError("retry_max_seconds must be greater than or equal to zero")
        if self.shutdown_timeout_seconds < 0:
            raise ValueError(
                "shutdown_timeout_seconds must be greater than or equal to zero"
            )


@dataclass(frozen=True, slots=True)
class AsyncIngestionBufferSnapshot:
    submitted: int
    delivered: int
    failed: int
    dropped_full: int
    retried: int
    max_depth: int
    current_depth: int
    closed: bool


@dataclass(frozen=True, slots=True)
class _BufferedCommand:
    command: CompressionActivityIngestCommand
    enqueued_at: float


class AsyncIngestionBuffer:
    """Bounded async command buffer with tracked workers and retry accounting."""

    def __init__(
        self,
        sink: CompressionActivitySink,
        config: AsyncIngestionBufferConfig,
    ) -> None:
        self._sink = sink
        self._config = config
        self._queue: asyncio.Queue[_BufferedCommand] | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._closed = False
        self._submitted = 0
        self._delivered = 0
        self._failed = 0
        self._dropped_full = 0
        self._retried = 0
        self._max_depth = 0

    def submit_nowait(self, command: CompressionActivityIngestCommand) -> bool:
        if self._closed:
            self._dropped_full += 1
            logger.debug("analytics buffer is closed; dropping command")
            return False
        queue = self._ensure_started()
        try:
            queue.put_nowait(_BufferedCommand(command=command, enqueued_at=time.time()))
        except asyncio.QueueFull:
            self._dropped_full += 1
            logger.warning(
                "analytics buffer full; dropping event_key=%s",
                command.event.event_key,
            )
            return False

        self._submitted += 1
        self._max_depth = max(self._max_depth, queue.qsize())
        return True

    async def flush(self, timeout_seconds: float | None = None) -> bool:
        queue = self._queue
        if queue is None:
            return True
        timeout = (
            self._config.shutdown_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        try:
            if timeout <= 0:
                await queue.join()
            else:
                await asyncio.wait_for(queue.join(), timeout=timeout)
        except TimeoutError:
            logger.warning("analytics buffer flush timed out; depth=%s", queue.qsize())
            return False
        return True

    async def aclose(self) -> None:
        self._closed = True
        await self.flush(self._config.shutdown_timeout_seconds)
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        close = getattr(self._sink, "aclose", None)
        if close is not None:
            await close()

    def snapshot(self) -> AsyncIngestionBufferSnapshot:
        queue = self._queue
        return AsyncIngestionBufferSnapshot(
            submitted=self._submitted,
            delivered=self._delivered,
            failed=self._failed,
            dropped_full=self._dropped_full,
            retried=self._retried,
            max_depth=self._max_depth,
            current_depth=queue.qsize() if queue is not None else 0,
            closed=self._closed,
        )

    def _ensure_started(self) -> asyncio.Queue[_BufferedCommand]:
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=self._config.max_queue_size)
        if not self._workers:
            for index in range(self._config.worker_count):
                self._workers.append(
                    asyncio.create_task(
                        self._worker_loop(index),
                        name=f"headroom-analytics-buffer-{index}",
                    )
                )
        return self._queue

    async def _worker_loop(self, worker_index: int) -> None:
        assert self._queue is not None
        queue = self._queue
        while True:
            item = await queue.get()
            try:
                await self._deliver(item, worker_index)
            finally:
                queue.task_done()

    async def _deliver(self, item: _BufferedCommand, worker_index: int) -> None:
        event_key = item.command.event.event_key
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                posted = await self._sink.post_compression_activity(item.command)
            except Exception as exc:
                posted = False
                logger.warning(
                    "analytics sink raised in worker=%s attempt=%s event_key=%s: %s",
                    worker_index,
                    attempt,
                    event_key,
                    exc,
                )
            if posted:
                self._delivered += 1
                return
            if attempt < self._config.max_attempts:
                self._retried += 1
                await asyncio.sleep(self._retry_delay(attempt))

        self._failed += 1
        logger.warning(
            "analytics delivery failed after retries event_key=%s", event_key
        )

    def _retry_delay(self, attempt: int) -> float:
        base = self._config.retry_base_seconds
        cap = self._config.retry_max_seconds
        delay = min(base * (2 ** (attempt - 1)), cap)
        if delay <= 0:
            return 0
        return delay + random.uniform(0, delay * 0.25)
