"""Sequential episode scheduling, resumable SQLite output and strict scoring."""

import asyncio
import json
from pathlib import Path

from ..core.ids import canonical, digest
from ..evaluation.diagnostics import diagnostics
from ..evaluation.extractors import extract
from ..persistence.event_store import EventStore
from ..persistence.lease import episode_lease
from ..policies.router import Router
from ..policies.scripted import Scripted
from ..scenarios import get_scenario
from .manifests import FIELDS, peer_slots
from .parallel import process_pool, scripted_only
from .runner import Runner
from .validation import is_model, scripted_name, validate_manifest


def society_overrides(manifest):
    """Disposition overrides the society layer gives each scripted slot."""
    from ..policies.scripted import ADVERSARIAL, UNCONDITIONAL

    society = manifest["layers"]["society"]
    peers = peer_slots(manifest)
    adversarial = set(peers[: round(len(peers) * society["adversarial_share"] / 100)])
    result = {}
    for slot, config in manifest["policies"].items():
        if is_model(config) or config == "oracle":
            continue
        overrides = dict(config.get("disposition", {})) if isinstance(config, dict) else {}
        overrides.update(society["dispositions"].get(scripted_name(config), {}))
        if slot in adversarial:
            overrides.update(ADVERSARIAL)
        if not society["conditional_cooperation"] and slot != manifest["focal_slot"]:
            overrides.update(UNCONDITIONAL)
        result[slot] = overrides
    return result


def bind(manifest):
    from ..core.random_tape import noise_seed
    from ..policies.ablations import Ablated
    from ..policies.llm_policy import LLMPolicy
    from ..policies.oracle import Oracle

    seed = noise_seed(manifest["seed"], manifest["replicate"])
    overrides = society_overrides(manifest)
    bindings = {}
    router = Router(bindings)
    for slot, config in manifest["policies"].items():
        if is_model(config):
            policy = LLMPolicy(config)
        elif config == "oracle":
            policy = Oracle(router, get_scenario(manifest["domain"]))
        else:
            name = scripted_name(config)
            policy = Scripted(
                dict(policy=name, disposition=overrides[slot]) if overrides[slot] else name,
                seed,
                slot == manifest["focal_slot"],
            )
        if manifest.get("ablations"):
            policy = Ablated(policy, manifest["ablations"])
        bindings[slot] = policy
    return router


async def run_episode(manifest, directory, until=None, concurrency=8, wrap=None):
    """Run one episode to completion; `wrap` may substitute policies after binding."""
    validate_manifest(manifest)
    scenario = get_scenario(manifest["domain"])
    certificate = scenario.feasible(manifest)
    path = Path(directory) / manifest["run_id"]
    path.mkdir(parents=True, exist_ok=True)
    manifest_path = path / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError("immutable_manifest_mismatch")
    manifest_path.write_text(canonical(manifest) + "\n")
    with episode_lease(path / "runner.lock"):
        (path / "feasibility.json").write_text(canonical(certificate) + "\n")
        store = EventStore(path / "episode.db")
        router = None
        try:
            router = bind(manifest)
            if wrap is not None:
                router = wrap(router)
            initial = scenario.build(manifest)
            preregistration = canonical(initial.scenario) + "\n"
            registration_path = path / "preregistration.json"
            if registration_path.exists() and registration_path.read_text() != preregistration:
                raise ValueError("preregistration_changed")
            registration_path.write_text(preregistration)
            store.initialize(manifest, initial)
            await Runner(store, scenario, router, concurrency).run(until)
            result = dict(
                manifest=manifest, **store.status(), verification=store.verify(), certified=False
            )
            result["diagnostics"] = diagnostics(store)
            if manifest["mode"] != "core":
                result["society_profile"] = result["diagnostics"]
                result["score_track"] = manifest["mode"]
            if result["status"] == "completed" and manifest["mode"] == "core":
                result["metrics"] = extract(store)
            (path / "result.json").write_text(canonical(result) + "\n")
            return result
        finally:
            store.close()
            for policy in router.bindings.values() if router else []:
                if hasattr(policy, "close"):
                    await policy.close()


def _run_episode_process(manifest, directory, until):
    return asyncio.run(run_episode(manifest, directory, until))


async def run_episode_offloaded(manifest, directory, until=None, wrap=None):
    """Scripted-only episodes run in a worker process; provider-backed ones stay inline."""
    pool = process_pool.get()
    if pool is not None and wrap is None and scripted_only(manifest):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            pool, _run_episode_process, manifest, str(directory), until
        )
    return await run_episode(manifest, directory, until, wrap=wrap)


def collect(plan, directory):
    spec = plan["suite"]
    result = dict(
        benchmark_id=spec["benchmark_id"],
        scaffold_id=spec["scaffold_id"],
        suite_hash=digest(spec),
        candidate_id=plan["candidate_id"],
        data_kind="empirical",
        budget_track="accounted_compute",
        runs=[],
    )
    for manifest in plan["episodes"]:
        store = EventStore(Path(directory) / manifest["run_id"] / "episode.db", read_only=True)
        try:
            if store.manifest != manifest:
                raise ValueError("manifest_mismatch")
            metrics = extract(store)
            result["runs"].append(
                dict(
                    {k: manifest[k] for k in FIELDS},
                    population=manifest["population"],
                    status="completed",
                    scores={k: v["normalized_value"] for k, v in metrics.items()},
                )
            )
            for config in manifest["policies"].values():
                if is_model(config) and config["budget_track"] == "opaque_compute":
                    result["budget_track"] = "opaque_compute"
        finally:
            store.close()
    return result
