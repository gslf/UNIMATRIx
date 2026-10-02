"""Bounded episode workers with a shared decision gate and resumable identities."""

import asyncio
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from contextvars import ContextVar

from ..policies.managed_models import ManagedModelPool, model_pool

request_gate = ContextVar("unimatrix_request_gate", default=None)
process_pool = ContextVar("unimatrix_process_pool", default=None)


def validate_parallelism(value):
    if type(value) is not int or not 1 <= value <= 64:
        raise ValueError("Parallel requests must be an integer between 1 and 64")
    return value


def worker_count():
    """Processes for scripted-only episodes; provider requests are bounded separately."""
    configured = os.environ.get("UNIMATRIX_WORKERS")
    if configured:
        return max(1, int(configured))
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def scripted_only(manifest):
    """No provider-backed policy: named policies and disposition objects alike."""
    policies = manifest.get("policies")
    return bool(policies) and not any(
        isinstance(p, dict) and "model" in p for p in policies.values()
    )


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
    remaining = [m for m in manifests if m["run_id"] not in completed]


    scripted = iter(m for m in remaining if scripted_only(m))
    provided = iter(m for m in remaining if not scripted_only(m))
    token = request_gate.set(asyncio.Semaphore(parallelism))
    pool = None
    pool_token = None
    if any(scripted_only(m) for m in remaining):
        pool = ProcessPoolExecutor(
            max_workers=worker_count(), mp_context=multiprocessing.get_context("spawn")
        )
        pool_token = process_pool.set(pool)

    async def consume(pending):
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

    counts = [
        (provided, min(parallelism, sum(not scripted_only(m) for m in remaining))),
        (scripted, min(worker_count(), sum(scripted_only(m) for m in remaining))),
    ]
    instances = ManagedModelPool()
    instance_token = model_pool.set(instances)
    tasks = [asyncio.create_task(consume(pending)) for pending, n in counts for _ in range(n)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            try:


                await instances.close()
            finally:
                model_pool.reset(instance_token)
                request_gate.reset(token)
                if pool is not None:
                    process_pool.reset(pool_token)

                    await asyncio.to_thread(pool.shutdown, True, cancel_futures=True)
