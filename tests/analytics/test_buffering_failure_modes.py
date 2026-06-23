import asyncio
from dataclasses import dataclass, field

import pytest

from litellm_proxy_headroom.analytics.application.buffering import (
    AsyncIngestionBuffer,
    AsyncIngestionBufferConfig,
)
from litellm_proxy_headroom.analytics.application.commands import (
    CompressionActivityIngestCommand,
    CompressionConfigCommand,
    CompressionExecutionCommand,
    CompressionRequestCommand,
    IngestionEventCommand,
)


def _command(event_key: str) -> CompressionActivityIngestCommand:
    return CompressionActivityIngestCommand(
        event=IngestionEventCommand(
            source="pytest",
            event_type="compression_result",
            event_key=event_key,
        ),
        request=CompressionRequestCommand(
            request_key=f"{event_key}-request",
            source_system="pytest",
        ),
        config=CompressionConfigCommand(
            config_hash=f"{event_key}-config",
            strategy_name="pytest-strategy",
        ),
        execution=CompressionExecutionCommand(
            attempt_number=1,
            status="succeeded",
        ),
    )


@dataclass(slots=True)
class SequencedSink:
    outcomes: list[bool]
    attempts: int = 0
    delivered_keys: list[str] = field(default_factory=list)

    async def post_compression_activity(
        self, command: CompressionActivityIngestCommand
    ) -> bool:
        self.attempts += 1
        outcome = self.outcomes.pop(0) if self.outcomes else True
        if outcome:
            self.delivered_keys.append(command.event.event_key)
        return outcome


class BlockingSink:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.delivered: list[str] = []

    async def post_compression_activity(
        self, command: CompressionActivityIngestCommand
    ) -> bool:
        self.started.set()
        await self.release.wait()
        self.delivered.append(command.event.event_key)
        return True


@pytest.mark.anyio
async def test_buffer_drops_when_bounded_queue_is_full() -> None:
    sink = BlockingSink()
    buffer = AsyncIngestionBuffer(
        sink,
        AsyncIngestionBufferConfig(
            max_queue_size=1,
            worker_count=1,
            max_attempts=1,
            shutdown_timeout_seconds=1,
        ),
    )

    assert buffer.submit_nowait(_command("one"))
    await sink.started.wait()
    assert buffer.submit_nowait(_command("two"))
    assert not buffer.submit_nowait(_command("three"))

    snapshot = buffer.snapshot()
    assert snapshot.submitted == 2
    assert snapshot.dropped_full == 1
    assert snapshot.max_depth == 1

    sink.release.set()
    assert await buffer.flush(timeout_seconds=1)
    await buffer.aclose()

    final = buffer.snapshot()
    assert final.delivered == 2
    assert final.failed == 0


@pytest.mark.anyio
async def test_buffer_retries_until_delivery_succeeds() -> None:
    sink = SequencedSink(outcomes=[False, False, True])
    buffer = AsyncIngestionBuffer(
        sink,
        AsyncIngestionBufferConfig(
            max_queue_size=10,
            worker_count=1,
            max_attempts=3,
            retry_base_seconds=0,
            retry_max_seconds=0,
            shutdown_timeout_seconds=1,
        ),
    )

    assert buffer.submit_nowait(_command("retry-success"))
    assert await buffer.flush(timeout_seconds=1)
    await buffer.aclose()

    snapshot = buffer.snapshot()
    assert sink.attempts == 3
    assert sink.delivered_keys == ["retry-success"]
    assert snapshot.delivered == 1
    assert snapshot.retried == 2
    assert snapshot.failed == 0


@pytest.mark.anyio
async def test_buffer_records_permanent_delivery_failure() -> None:
    sink = SequencedSink(outcomes=[False, False])
    buffer = AsyncIngestionBuffer(
        sink,
        AsyncIngestionBufferConfig(
            max_queue_size=10,
            worker_count=1,
            max_attempts=2,
            retry_base_seconds=0,
            retry_max_seconds=0,
            shutdown_timeout_seconds=1,
        ),
    )

    assert buffer.submit_nowait(_command("retry-failure"))
    assert await buffer.flush(timeout_seconds=1)
    await buffer.aclose()

    snapshot = buffer.snapshot()
    assert sink.attempts == 2
    assert snapshot.delivered == 0
    assert snapshot.retried == 1
    assert snapshot.failed == 1


@pytest.mark.anyio
async def test_buffer_delivers_concurrent_submissions() -> None:
    sink = SequencedSink(outcomes=[])
    buffer = AsyncIngestionBuffer(
        sink,
        AsyncIngestionBufferConfig(
            max_queue_size=50,
            worker_count=4,
            max_attempts=1,
            shutdown_timeout_seconds=2,
        ),
    )

    async def submit(index: int) -> bool:
        return buffer.submit_nowait(_command(f"concurrent-{index}"))

    results = await asyncio.gather(*(submit(index) for index in range(25)))
    assert all(results)
    assert await buffer.flush(timeout_seconds=2)
    await buffer.aclose()

    snapshot = buffer.snapshot()
    assert snapshot.submitted == 25
    assert snapshot.delivered == 25
    assert snapshot.failed == 0
    assert snapshot.dropped_full == 0
