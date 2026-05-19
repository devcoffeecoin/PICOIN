from __future__ import annotations

import asyncio
import inspect
import itertools
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.services.consensus import propose_block


ProposalProcessor = Callable[[dict[str, Any], str, bool], dict[str, Any] | Awaitable[dict[str, Any]]]


@dataclass(order=True)
class _QueuedProposal:
    height: int
    sequence: int
    block: dict[str, Any] = field(compare=False)
    proposer_node_id: str = field(compare=False)
    gossip: bool = field(compare=False)
    future: asyncio.Future[dict[str, Any]] = field(compare=False)


class ConsensusProposalQueue:
    def __init__(
        self,
        processor: ProposalProcessor = propose_block,
        *,
        coalesce_seconds: float = 0.025,
    ) -> None:
        self._processor = processor
        self._coalesce_seconds = coalesce_seconds
        self._queue: asyncio.PriorityQueue[_QueuedProposal] = asyncio.PriorityQueue()
        self._sequence = itertools.count()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="picoin-consensus-proposal-queue")

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    async def submit(self, block: dict[str, Any], proposer_node_id: str, gossip: bool = True) -> dict[str, Any]:
        await self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        height = int(block.get("height") or 0)
        await self._queue.put(
            _QueuedProposal(
                height=height,
                sequence=next(self._sequence),
                block=dict(block),
                proposer_node_id=proposer_node_id,
                gossip=gossip,
                future=future,
            )
        )
        return await future

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if self._coalesce_seconds > 0:
                await asyncio.sleep(self._coalesce_seconds)
            pending = [item]
            while not self._queue.empty():
                pending.append(await self._queue.get())
            pending.sort()
            item = pending[0]
            for delayed in pending[1:]:
                self._queue.task_done()
                await self._queue.put(delayed)
            try:
                if inspect.iscoroutinefunction(self._processor):
                    result = await self._processor(item.block, item.proposer_node_id, item.gossip)  # type: ignore[misc]
                else:
                    result = await asyncio.to_thread(
                        self._processor,
                        item.block,
                        item.proposer_node_id,
                        item.gossip,
                    )
                if not item.future.done():
                    item.future.set_result(result)
            except Exception as exc:
                if not item.future.done():
                    item.future.set_exception(exc)
            finally:
                self._queue.task_done()


_default_queue = ConsensusProposalQueue()


async def start_consensus_queue() -> None:
    await _default_queue.start()


async def stop_consensus_queue() -> None:
    await _default_queue.stop()


async def submit_block_proposal(
    block: dict[str, Any],
    proposer_node_id: str,
    gossip: bool = True,
) -> dict[str, Any]:
    return await _default_queue.submit(block, proposer_node_id, gossip)
