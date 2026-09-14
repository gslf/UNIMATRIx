"""Sequential episode scheduling, resumable SQLite output and strict scoring."""

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
from .manifests import FIELDS
from .runner import Runner
from .validation import validate_manifest


def bind(manifest):
    from ..policies.ablations import Ablated
    from ..policies.llm_policy import LLMPolicy

    bindings = {}
    for slot, config in manifest["policies"].items():
        policy = Scripted(config) if isinstance(config, str) else LLMPolicy(config)
        if manifest.get("ablations"):
            policy = Ablated(policy, manifest["ablations"])
        bindings[slot] = policy
    return Router(bindings)


async def run_episode(manifest, directory, until=None, concurrency=8):
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
                if isinstance(config, dict) and config["budget_track"] == "opaque_compute":
                    result["budget_track"] = "opaque_compute"
        finally:
            store.close()
    return result
