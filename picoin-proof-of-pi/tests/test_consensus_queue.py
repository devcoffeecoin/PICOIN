import asyncio

from app.services.consensus_queue import ConsensusProposalQueue


def test_consensus_queue_processes_burst_by_block_height() -> None:
    processed: list[int] = []

    async def processor(block: dict, proposer_node_id: str, gossip: bool) -> dict:
        processed.append(int(block["height"]))
        return {"height": int(block["height"]), "proposer_node_id": proposer_node_id, "gossip": gossip}

    async def run_queue() -> list[dict]:
        queue = ConsensusProposalQueue(processor=processor, coalesce_seconds=0.01)
        await queue.start()
        try:
            second = asyncio.create_task(queue.submit({"height": 2}, "peer-two", False))
            first = asyncio.create_task(queue.submit({"height": 1}, "peer-one", False))
            return await asyncio.gather(second, first)
        finally:
            await queue.stop()

    results = asyncio.run(run_queue())

    assert processed == [1, 2]
    assert [result["height"] for result in results] == [2, 1]
