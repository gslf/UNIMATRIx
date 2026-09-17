"""Bounded episode workers with a shared decision gate and resumable identities."""

import asyncio
from contextvars import ContextVar

request_gate = ContextVar("unimatrix_request_gate", default=None)


def validate_parallelism(value):
    if type(value) is not int or not 1 <= value <= 64:
        raise ValueError("Parallel requests must be an integer between 1 and 64")
    return value


async def execute_episodes(record, manifests, worker, save, parallelism, totals_key, metrics):
    parallelism = validate_parallelism(parallelism)
    completed = set(
        record.get(
            "completed_episode_ids",
            [m["run_id"] for m in manifests[: record["completed_episodes"]]],
        )
    )
    record["completed_episode_ids"] = sorted(completed)
    record["current_episodes"] = []
    record["current_episode"] = None
    pending = iter(m for m in manifests if m["run_id"] not in completed)
    token = request_gate.set(asyncio.Semaphore(parallelism))

    async def consume():
        for manifest in pending:
            ident = manifest["run_id"]
            record["current_episodes"].append(ident)
            record["current_episode"] = record["current_episodes"][0]
            save()
            diagnostics = await worker(manifest)
            totals = record.setdefault(totals_key, {})
            for key in metrics:
                totals[key] = totals.get(key, 0) + diagnostics[key]
            completed.add(ident)
            record["completed_episode_ids"] = sorted(completed)
            record["completed_episodes"] = len(completed)
            record["current_episodes"].remove(ident)
            record["current_episode"] = next(iter(record["current_episodes"]), None)
            save()

    tasks = [asyncio.create_task(consume()) for _ in range(min(parallelism, len(manifests)))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        request_gate.reset(token)
