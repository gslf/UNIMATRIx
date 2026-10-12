"""Run a candidate model as steward, durably: every decision is appended to a log before it
is applied, so an interrupted run resumes by replaying the log through the deterministic engine."""

import asyncio
import json
import os
import time
from functools import lru_cache
from pathlib import Path

from ..core.ids import canonical, digest
from .actions import parse_decision
from .report import build_report
from .world import World

RETRY_BACKOFF = (1.0, 4.0)


@lru_cache(maxsize=1)
def engine_fingerprint():
    root = Path(__file__).resolve().parent
    files = {p.name: p.read_text() for p in sorted(root.glob("*.py"))}
    return digest(files)


class ReplayMismatch(ValueError):
    pass


def read_log(path):
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                break
    return rows


def append_log(path, row):
    with path.open("a", encoding="utf8") as stream:
        stream.write(canonical(row) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def live_view(world):
    """Everything a live viewer needs: full frames (for the animated village) and the cast."""
    cast = [dict(id=r["id"], name=r["name"], age=r["age"], role=r["role"], faction=r["faction"],
                 newcomer=r["newcomer"], bio=r["bio"]) for r in world.residents.values()]
    return dict(frames=world.frames, residents=cast)


async def decide(policy, observation, attempts=3):
    from ..models.transport import InfrastructureFailure

    last = None
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF)) - 1])
        started = time.monotonic()
        try:
            raw, usage = await policy.decide(observation)
        except InfrastructureFailure as error:
            last = error
            if not error.retryable:
                raise
            continue
        return raw, dict(usage or {}, latency=round(time.monotonic() - started, 3), attempt=attempt)
    raise last or RuntimeError("retry_budget_exhausted")


async def run_seed(recipe, seed, policy, folder, on_week=None, meta=None):
    """Play one seed to the end (or to collapse); resume from `folder/decisions.jsonl`."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / "decisions.jsonl"
    world = World(recipe, seed)
    for row in read_log(log):
        observation = world.observe()
        if observation is None:
            break
        if digest(observation) != row["observation_hash"]:
            raise ReplayMismatch(f"replay diverged at week {row['week']}; the engine or recipe changed")
        decision, error = parse_decision(row["raw"])
        world.step(decision, row["raw"], error)
    if on_week:
        on_week(world)
    while not world.done:
        observation = world.observe()
        if observation is None:
            break
        raw, usage = await decide(policy, observation)
        decision, error = parse_decision(raw)
        append_log(log, dict(week=world.week, observation_hash=digest(observation), raw=raw, usage=usage,
                             parse_error=error, observation=observation))
        world.step(decision, raw, error)
        if on_week:
            on_week(world)
        await asyncio.sleep(0)
    report = build_report(world, meta)
    report["frames"] = world.frames
    (folder / "report.json").write_text(canonical(report) + "\n")
    return report, world


def replay_seed(recipe, seed, folder):
    """Rebuild a recorded seed without calling any provider; verifies the log."""
    world = World(recipe, seed)
    for row in read_log(Path(folder) / "decisions.jsonl"):
        observation = world.observe()
        if observation is None:
            break
        if digest(observation) != row["observation_hash"]:
            raise ReplayMismatch(f"replay diverged at week {row['week']}")
        decision, error = parse_decision(row["raw"])
        world.step(decision, row["raw"], error)
    return world
