"""Frozen research campaigns and measured diagnostics for plan development."""

import asyncio
from collections import defaultdict
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from statistics import fmean

from ..benchmark.fingerprints import runtime_fingerprint
from ..benchmark.parallel import execute_episodes, validate_parallelism
from ..benchmark.plans import bind_candidate
from ..benchmark.runner import Runner
from ..benchmark.scheduler import bind, collect
from ..benchmark.service import now
from ..core.ids import digest
from ..evaluation.diagnostics import diagnostics
from ..evaluation.scoring import bootstrap, domain_means, interval, summarize, validate
from ..persistence.event_store import EventStore
from ..persistence.json_files import read_json, write_json
from ..persistence.lease import episode_lease
from ..scenarios import get_scenario
from .interventions import IntervenedPolicy

BASELINES = ("passive", "random", "independent", "greedy", "reciprocal", "coordinator")
INTERVENTIONS = ("no_communication", "no_memory", "reverse_observation_order", "rename_agents")


def research_fingerprint():
    return digest({p.name: p.read_text() for p in sorted(Path(__file__).parent.glob("*.py"))})


def prepare(spec, models, baselines, interventions, name, parallelism=1):
    validate_parallelism(parallelism)
    if not name.strip() or len(name) > 120:
        raise ValueError("A campaign name is required (up to 120 characters)")
    if set(baselines) - set(BASELINES) or len(baselines) != len(set(baselines)):
        raise ValueError("Unknown or duplicate baseline")
    if set(interventions) - set(INTERVENTIONS) or len(interventions) != len(set(interventions)):
        raise ValueError("Unknown or duplicate intervention")
    if not models and not baselines:
        raise ValueError("Choose at least one baseline or model")
    if len(models) > 8:
        raise ValueError("Use at most eight models per campaign")
    systems = [("baseline/" + p, p) for p in baselines] + list(models.items())
    count = len(baselines) + len(models) * (1 + len(interventions))
    if count * len(spec["cases"]) > 20000:
        raise ValueError(
            "A campaign can contain at most 20,000 episodes. Reduce cases, models or interventions."
        )
    studies = []
    identities = set()
    for system_id, candidate in systems:
        execution = bind_candidate(spec, candidate)
        if execution["candidate_id"] in identities:
            raise ValueError("Two model files describe the same candidate; select it once")
        identities.add(execution["candidate_id"])
        for intervention in [None] + (interventions if isinstance(candidate, dict) else []):
            study = deepcopy(execution)
            # The marker isolates research run identities. The research runner below
            # applies the intervention to the focal candidate, never to its peers.
            study["suite"] = dict(
                study["suite"],
                research_variant=intervention or "normal",
                intervention_scope="candidate",
                research_runtime=research_fingerprint(),
            )
            for manifest in study["episodes"]:
                manifest["suite_hash"] = digest(study["suite"])
                manifest["run_id"] = digest({k: v for k, v in manifest.items() if k != "run_id"})[
                    :24
                ]
            studies.append(
                dict(
                    id=str(len(studies)),
                    system_id=system_id,
                    label=candidate["model"] if isinstance(candidate, dict) else candidate,
                    intervention=intervention,
                    execution=study,
                    status="pending",
                    completed_episodes=0,
                    current_episode=None,
                    report=None,
                    error=None,
                )
            )
    provider_decisions = sum(
        240 * sum(isinstance(p, dict) for p in manifest["policies"].values())
        for study in studies
        for manifest in study["execution"]["episodes"]
    )
    contexts = [
        policy.get("context_tokens")
        for study in studies
        for manifest in study["execution"]["episodes"]
        for policy in manifest["policies"].values()
        if isinstance(policy, dict)
    ]
    generated_max = None if any(c is None for c in contexts) else sum(contexts) * 240 * 3
    return dict(
        name=name,
        parallelism=parallelism,
        plan=deepcopy(spec),
        plan_hash=digest(spec),
        studies=studies,
        runtime=runtime_fingerprint(),
        research_runtime=research_fingerprint(),
        status="pending",
        created_at=now(),
        updated_at=now(),
        episodes=sum(len(s["execution"]["episodes"]) for s in studies),
        provider_decisions_max=provider_decisions,
        provider_attempts_max=provider_decisions * 3,
        generated_tokens_max=generated_max,
        analysis=None,
    )


async def run_research_episode(manifest, directory, intervention):
    """Use the canonical engine with a candidate-only research wrapper."""
    from ..benchmark.validation import validate_manifest

    validate_manifest(manifest)
    folder = Path(directory) / manifest["run_id"]
    folder.mkdir(parents=True, exist_ok=True)
    with episode_lease(folder / "runner.lock"):
        expected = dict(manifest=manifest, intervention=intervention, scope="candidate")
        record = folder / "research.json"
        if record.exists() and read_json(record) != expected:
            raise ValueError("Research intervention or manifest changed")
        write_json(record, expected)
        scenario = get_scenario(manifest["domain"])
        write_json(folder / "feasibility.json", scenario.feasible(manifest))
        store = EventStore(folder / "episode.db")
        policies = None
        try:
            initial = scenario.build(manifest)
            write_json(folder / "preregistration.json", initial.scenario)
            store.initialize(manifest, initial)
            policies = bind(manifest)
            if intervention:
                slot = manifest["focal_slot"]
                policies.bindings[slot] = IntervenedPolicy(
                    policies.bindings[slot], intervention, research_fingerprint()
                )
            await Runner(store, scenario, policies).run()
            return diagnostics(store)
        finally:
            store.close()
            for policy in policies.bindings.values() if policies else []:
                if hasattr(policy, "close"):
                    await policy.close()


def analyze(campaign, folder):
    complete, metrics, effects, notes = {}, defaultdict(list), [], []
    for study in campaign["studies"]:
        if study["status"] != "completed":
            continue
        data = read_json(folder / "studies" / study["id"] / "results.json")
        spec = study["execution"]["suite"]
        values = validate(data, spec)
        complete[(study["system_id"], study["intervention"])] = (data, values, spec, study)
        if study["intervention"] is None:
            for row in data["runs"]:
                for metric, score in row["scores"].items():
                    metrics[metric].append(score)
    for (system_id, intervention), (_, values, _, study) in complete.items():
        if intervention and (system_id, None) in complete:
            _, baseline, spec, original = complete[(system_id, None)]
            samples, _ = bootstrap(baseline, spec, values)
            left, right = domain_means(baseline, spec), domain_means(values, spec)
            effects.append(
                dict(
                    system=study["label"],
                    system_id=system_id,
                    intervention=intervention,
                    delta=100 * fmean(left[d] - right[d] for d in left),
                    ci95=interval(samples),
                )
            )
    health = [
        dict(
            metric=k,
            mean=100 * fmean(v),
            minimum=100 * min(v),
            maximum=100 * max(v),
            ceiling_fraction=sum(x >= 0.98 for x in v) / len(v),
            floor_fraction=sum(x <= 0.02 for x in v) / len(v),
            observations=len(v),
        )
        for k, v in sorted(metrics.items())
    ]
    normals = [entry[3] for (ident, variant), entry in complete.items() if variant is None]
    model_normals = [s for s in normals if not s["system_id"].startswith("baseline/")]
    if len(model_normals) < 3:
        notes.append(
            "Test at least three distinct models before judging how well this plan separates competitors."
        )
    if len({c["seed"] for c in campaign["plan"]["cases"]}) < 4:
        notes.append(
            "There are fewer than four tested seeds; confidence intervals may be uninformative."
        )
    saturated = [m["metric"] for m in health if m["ceiling_fraction"] >= 0.8]
    floors = [m["metric"] for m in health if m["floor_fraction"] >= 0.8]
    if saturated:
        notes.append(
            "Frequent maximum scores: " + ", ".join(saturated) + ". Consider harder cases."
        )
    if floors:
        notes.append(
            "Frequent zero scores: "
            + ", ".join(floors)
            + ". Inspect decision validity and task difficulty."
        )
    if effects and all(e["ci95"][0] <= 0 <= e["ci95"][1] for e in effects):
        notes.append(
            "No intervention has a clear paired effect in this sample. More seeds or different cases may be needed."
        )
    failed = sum(s["status"] == "failed" for s in campaign["studies"])
    if failed:
        notes.append(
            f"{failed} studies failed. Their missing scores are excluded, not replaced with zero."
        )
    notes.append(
        "These are research diagnostics, not a certification. Confirm changes on untouched holdout seeds."
    )
    return dict(
        completed_studies=len(complete),
        total_studies=len(campaign["studies"]),
        effects=effects,
        metric_health=health,
        notes=notes,
        score_spread=max((s["report"]["usi"] for s in model_normals), default=0)
        - min((s["report"]["usi"] for s in model_normals), default=0)
        if len(model_normals) >= 2
        else None,
    )


class CampaignRunner:
    def __init__(self, directory, shared_lock):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.shared_lock = shared_lock
        self.active = {}

    def save(self, campaign):
        campaign["updated_at"] = now()
        write_json(self.directory / campaign["id"] / "campaign.json", campaign)

    def launch(self, campaign):
        if self.active:
            raise ValueError("A research campaign is already running")
        if (
            campaign["runtime"] != runtime_fingerprint()
            or campaign["research_runtime"] != research_fingerprint()
        ):
            raise ValueError(
                "The engine changed. Create a new campaign; previous findings remain available."
            )
        resources = ExitStack()
        try:
            resources.enter_context(episode_lease(self.shared_lock))
            campaign.update(status="running", error=None)
            self.save(campaign)
            task = asyncio.create_task(self.execute(campaign))
            self.active[campaign["id"]] = (task, resources)
        except BaseException:
            resources.close()
            raise

    async def execute(self, campaign):
        folder = self.directory / campaign["id"]
        try:
            for study in campaign["studies"]:
                if study["status"] in {"completed", "failed"}:
                    continue
                study["status"] = "running"
                study_folder = folder / "studies" / study["id"]
                try:

                    async def worker(manifest):
                        return await run_research_episode(
                            manifest, study_folder / "episodes", study["intervention"]
                        )

                    await execute_episodes(
                        study,
                        study["execution"]["episodes"],
                        worker,
                        lambda: self.save(campaign),
                        campaign.get("parallelism", 1),
                        "diagnostics",
                        [
                            "invalid_envelopes",
                            "rejected_operations",
                            "messages",
                            "provider_attempts",
                        ],
                    )
                    data = await asyncio.to_thread(
                        collect, study["execution"], study_folder / "episodes"
                    )
                    study["report"] = await asyncio.to_thread(
                        summarize, data, study["execution"]["suite"]
                    )
                    write_json(study_folder / "results.json", data)
                    study["status"] = "completed"
                except asyncio.CancelledError:
                    study["status"] = "paused"
                    raise
                except Exception as error:
                    study.update(status="failed", error=f"{type(error).__name__}: {error}")
                self.save(campaign)
            campaign["analysis"] = await asyncio.to_thread(analyze, campaign, folder)
            campaign["status"] = (
                "completed_with_failures"
                if any(s["status"] == "failed" for s in campaign["studies"])
                else "completed"
            )
        except asyncio.CancelledError:
            campaign["status"] = "paused"
            raise
        except Exception as error:
            campaign.update(status="failed", error=f"{type(error).__name__}: {error}")
        finally:
            try:
                self.save(campaign)
            finally:
                active = self.active.pop(campaign["id"], None)
                if active:
                    active[1].close()

    async def pause(self, campaign):
        active = self.active.get(campaign["id"])
        if active:
            active[0].cancel()
            await asyncio.gather(active[0], return_exceptions=True)
            if campaign["id"] in self.active:
                self.active.pop(campaign["id"])[1].close()
                campaign["status"] = "paused"
                self.save(campaign)

    async def shutdown(self):
        for ident in list(self.active):
            await self.pause(read_json(self.directory / ident / "campaign.json"))
