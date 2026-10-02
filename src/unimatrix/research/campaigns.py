"""Frozen research campaigns and measured diagnostics for plan development."""

import asyncio
import math
from collections import defaultdict
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from statistics import fmean

from ..benchmark.fingerprints import runtime_fingerprint
from ..benchmark.parallel import execute_episodes, validate_parallelism
from ..benchmark.recipes import bind_candidate
from ..benchmark.scheduler import collect, run_episode_offloaded
from ..benchmark.service import now
from ..benchmark.validation import is_model, scripted_name
from ..core.ids import digest
from ..evaluation.scoring import (
    case_key,
    common_cases,
    compare,
    domain_means,
    summarize,
    validate,
)
from ..evaluation.stats import discrimination as cell_discrimination
from ..evaluation.uncertainty import METHOD, seed_interval
from ..persistence.json_files import read_json, write_json
from ..persistence.lease import episode_lease
from ..scenarios.layers import layers_key
from .interventions import IntervenedPolicy

BASELINES = ("passive", "random", "independent", "greedy", "reciprocal", "coordinator", "oracle")
MODES = ("smoke", "pilot", "full")
STOPPING = dict(reference="reciprocal", minimum_effect=5, block_seeds=4, max_seeds=32)
FLOORS = ("baseline/random", "baseline/passive")
CEILINGS = (
    "baseline/oracle",
    "baseline/reciprocal",
    "baseline/coordinator",
    "baseline/greedy",
    "baseline/prudent",
    "baseline/independent",
)
INTERVENTIONS = ("no_communication", "no_memory", "reverse_observation_order", "rename_agents")


def research_fingerprint():
    return digest({p.name: p.read_text() for p in sorted(Path(__file__).parent.glob("*.py"))})


def validate_stopping(value):
    stopping = dict(STOPPING, **(value or {}))
    if set(stopping) != set(STOPPING) or stopping["reference"] not in BASELINES:
        raise ValueError("Invalid stopping rule")
    effect = stopping["minimum_effect"]
    if type(effect) not in (int, float) or not 0 < effect <= 50:
        raise ValueError("The minimum effect must be between 0 and 50 points")
    for key in ("block_seeds", "max_seeds"):
        if type(stopping[key]) is not int or not 1 <= stopping[key] <= 32:
            raise ValueError("Seed counts must be integers between 1 and 32")
    return stopping


def smoke_cases(spec):
    """One case per domain and complexity: the first seed, role and replicate."""
    chosen, cases = set(), []
    for case in spec["cases"]:
        key = (case["domain"], layers_key(case["layers"]))
        if key not in chosen:
            chosen.add(key)
            cases.append(case)
    return dict(spec, cases=cases)


def prepare(
    spec,
    models,
    baselines,
    interventions,
    name,
    parallelism=1,
    mode="full",
    stopping=None,
    profiles=(),
):
    validate_parallelism(parallelism)
    if mode not in MODES:
        raise ValueError("Unknown evaluation mode")
    stopping = validate_stopping(stopping) if mode == "pilot" else None
    if mode == "smoke":
        spec = smoke_cases(spec)
    if mode == "pilot" and stopping["reference"] not in baselines:
        baselines = list(baselines) + [stopping["reference"]]
    if not name.strip() or len(name) > 120:
        raise ValueError("An evaluation name is required (up to 120 characters)")
    if set(baselines) - set(BASELINES) or len(baselines) != len(set(baselines)):
        raise ValueError("Unknown or duplicate baseline")
    if set(interventions) - set(INTERVENTIONS) or len(interventions) != len(set(interventions)):
        raise ValueError("Unknown or duplicate intervention")
    if not models and not baselines:
        raise ValueError("Choose at least one baseline or model")
    if len(models) > 32:
        raise ValueError("Use at most 32 models per evaluation")
    if len(profiles) != len(set(profiles)) or set(profiles) - set(
        spec.get("candidate_profiles", {})
    ):
        raise ValueError("Choose distinct candidate profiles from this recipe")
    if profiles:
        from ..benchmark.personas import apply_profile

        models = {
            ident + "/profile/" + profile: apply_profile(
                config, spec["candidate_profiles"], profile
            )
            for ident, config in models.items()
            for profile in profiles
        }
    systems = [("baseline/" + p, p) for p in baselines] + list(models.items())
    count = len(baselines) + len(models) * (1 + len(interventions))
    if count * len(spec["cases"]) > 20000:
        raise ValueError(
            "An evaluation can contain at most 20,000 episodes. Reduce cases, models or interventions."
        )
    studies = []
    identities = set()
    for system_id, candidate in systems:
        execution = bind_candidate(spec, candidate)
        if execution["candidate_id"] in identities:
            raise ValueError("Two model files describe the same candidate; select it once")
        identities.add(execution["candidate_id"])
        for intervention in [None] + (interventions if is_model(candidate) else []):
            study = deepcopy(execution)


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
                    model_hash=digest(candidate) if is_model(candidate) else None,
                    label=(
                        candidate["model"]
                        + (" · " + candidate["personality"] if candidate.get("personality") else "")
                    )
                    if is_model(candidate)
                    else scripted_name(candidate),
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
        manifest["ticks"] * sum(is_model(p) for p in manifest["policies"].values())
        for study in studies
        for manifest in study["execution"]["episodes"]
    )
    contexts = [
        policy.get("max_output_tokens", policy.get("context_tokens"))
        for study in studies
        for manifest in study["execution"]["episodes"]
        for policy in manifest["policies"].values()
        if is_model(policy)
    ]
    generated_max = None if any(c is None for c in contexts) else sum(contexts) * 240 * 3
    generated_max = None if generated_max is None else generated_max // 240 * spec["ticks"]
    return dict(
        name=name,
        parallelism=parallelism,
        mode=mode,
        stopping=stopping,
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
        budget_scope="Per automatic retry window; manual resumes retain and add to total cost.",
        generated_tokens_max=generated_max,
        analysis=None,
    )


async def run_research_episode(manifest, directory, intervention):
    """Use the canonical engine with a candidate-only research wrapper."""
    folder = Path(directory) / manifest["run_id"]
    folder.mkdir(parents=True, exist_ok=True)
    expected = dict(manifest=manifest, intervention=intervention, scope="candidate")
    record = folder / "research.json"
    if record.exists() and read_json(record) != expected:
        raise ValueError("Research intervention or manifest changed")
    write_json(record, expected)

    def wrap(router):
        slot = manifest["focal_slot"]
        router.bindings[slot] = IntervenedPolicy(
            router.bindings[slot], intervention, research_fingerprint()
        )
        return router

    result = await run_episode_offloaded(manifest, directory, wrap=wrap if intervention else None)
    if result["status"] != "completed":
        raise ValueError("Episode did not complete")
    return result["diagnostics"]


def wilson(successes, trials, z=1.96):
    if not trials:
        return [0, 1]
    p = successes / trials
    centre = (p + z * z / (2 * trials)) / (1 + z * z / trials)
    spread = (
        z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / (1 + z * z / trials)
    )
    return [round(max(0, centre - spread), 4), round(min(1, centre + spread), 4)]


def persona_alignment(normals):
    """Manipulation check: do paired differences between personas of one model follow
    the signs their presets declare? One row per persona pair."""
    from ..benchmark.personas import catalog

    personas = catalog()
    groups = {}
    for sid, (data, values, spec, study) in normals.items():
        manifest = study["execution"]["episodes"][0]
        candidate = manifest["policies"][manifest["focal_slot"]]
        if not is_model(candidate):
            continue
        personas.update(spec.get("candidate_profiles", {}))
        if candidate.get("persona") not in personas:
            continue
        identity = (candidate["model"], candidate["snapshot"])
        rows = {case_key(r): r["scores"] for r in data["runs"]}
        groups.setdefault(identity, []).append((candidate["persona"], rows, study["label"]))
    result = []
    for (model, _), members in groups.items():
        for i, (left, left_rows, _) in enumerate(members):
            for right, right_rows, _ in members[i + 1 :]:
                expected = {}
                signs_left = personas[left]["expected_signs"]
                signs_right = personas[right]["expected_signs"]
                for metric in set(signs_left) | set(signs_right):
                    value = {"+": 1, "0": 0, "-": -1}
                    gap = value[signs_left.get(metric, "0")] - value[signs_right.get(metric, "0")]
                    if gap:
                        expected[metric] = 1 if gap > 0 else -1
                checked = matched = 0
                shared = set(left_rows) & set(right_rows)
                for metric, direction in expected.items():
                    deltas = [
                        left_rows[k][metric] - right_rows[k][metric]
                        for k in shared
                        if metric in left_rows[k] and metric in right_rows[k]
                    ]
                    if not deltas:
                        continue
                    checked += 1
                    observed = fmean(deltas)
                    matched += (observed > 0) == (direction > 0) and observed != 0
                result.append(
                    dict(
                        model=model,
                        left=left,
                        right=right,
                        checked=checked,
                        matched=matched,
                        alignment=matched / checked if checked else None,
                        ci95=None,
                        scope="Descriptive agreement of correlated metrics; no binomial independence assumption.",
                    )
                )
    return result


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
            left, right = domain_means(baseline, spec), domain_means(values, spec)
            effects.append(
                dict(
                    system=study["label"],
                    system_id=system_id,
                    intervention=intervention,
                    delta=100 * fmean(left[d] - right[d] for d in left),
                    ci95=[100*v for v in seed_interval(
                        {k: baseline[k] - values[k] for k in values}, paired=True,
                        comparisons=max(1, len(complete)))],
                    interval_method=METHOD,
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
    normals = {sid: entry for (sid, variant), entry in complete.items() if variant is None}
    floors = [normals[sid][1] for sid in FLOORS if sid in normals]
    ceilings = [normals[sid][1] for sid in CEILINGS if sid in normals]
    references = None
    if floors and ceilings:


        keys = set.intersection(*(set(v) for v in floors + ceilings))
        references = {
            key: (min(v[key] for v in floors), max(v[key] for v in ceilings)) for key in keys
        }
    systems = []
    for sid, (data, values, spec, study) in normals.items():
        report = summarize(data, spec, references)
        systems.append(
            dict(
                system=study["label"],
                system_id=sid,
                usi=report["usi"],
                iqm=report["iqm"],
                optimality_gap=report["optimality_gap"],
                rating_points=100 * report["rating"] if report.get("rating") is not None else None,
                rating=report.get("rating"),
                degenerate_cells=report.get("degenerate_cells"),
            )
        )
    model_ids = [sid for sid in normals if not sid.startswith("baseline/")]
    comparisons = []
    for left in model_ids:
        for right in normals:
            if right == left or (
                right in model_ids and model_ids.index(right) < model_ids.index(left)
            ):
                continue
            a, b, shared = common_cases(
                normals[left][0], normals[left][2], normals[right][0], normals[right][2]
            )
            result = compare(a, b, shared, comparisons=max(1, len(normals) * (len(normals)-1) // 2))
            low, high = result["paired_ci95"]
            comparisons.append(
                dict(
                    left=normals[left][3]["label"],
                    right=normals[right][3]["label"],
                    delta=result["paired_delta"],
                    ci95=result["paired_ci95"],
                    seed_rho=result["seed_rho"],
                    probability=result["probability_of_improvement"],
                    verdict="significant" if low > 0 or high < 0 else "inconclusive",
                )
            )
    cells = {}
    for sid, (_, values, _, _) in normals.items():
        for (domain, stratum, seed, role, _), score in values.items():
            cells.setdefault((domain, stratum, seed, role), {}).setdefault(sid, []).append(score)
    discrimination = []
    for (domain, stratum, seed, role), scores in sorted(cells.items()):
        row = cell_discrimination(scores)
        models = {sid: v for sid, v in scores.items() if sid in model_ids}
        model_spread = cell_discrimination(models)["spread"] if len(models) > 1 else None
        degenerate = None
        if references:
            bounds = [b for k, b in references.items() if k[:4] == (domain, stratum, seed, role)]
            degenerate = bool(bounds) and all(c - f < 0.05 for f, c in bounds)
        discrimination.append(
            dict(
                domain=domain,
                complexity=stratum,
                seed=seed,
                role=role,
                spread=100 * row["spread"],
                model_spread=None if model_spread is None else 100 * model_spread,
                degenerate=degenerate,
            )
        )
    if references is None:
        notes.append(
            "Select the random baseline and at least one reference (oracle, reciprocal) to "
            "normalize scores between floor and ceiling."
        )
    degenerate_cells = [c for c in discrimination if c["degenerate"]]
    if degenerate_cells:
        notes.append(
            "No room between the random floor and the oracle ceiling in: "
            + ", ".join(
                f"{c['domain']}/{c['complexity']}/seed {c['seed']}/{c['role']}"
                for c in degenerate_cells[:5]
            )
            + ". Harden these cells with a stricter layer."
        )
    flat_cells = [
        c for c in discrimination if c["model_spread"] is not None and c["model_spread"] < 5
    ]
    if flat_cells:
        notes.append(
            "Cells that do not separate the tested models: "
            + ", ".join(
                f"{c['domain']}/{c['complexity']}/seed {c['seed']}/{c['role']}"
                for c in flat_cells[:5]
            )
            + "."
        )
    alignment = persona_alignment(normals)
    for row in alignment:
        if row["alignment"] is not None and row["alignment"] <= 0.5:
            notes.append(
                f"Persona {row['left']} vs {row['right']} on {row['model']}: observed differences "
                f"follow the declared expectations in {row['matched']}/{row['checked']} metrics; "
                "treat these presets as flavour text until they separate."
            )
    if comparisons and all(c["verdict"] == "inconclusive" for c in comparisons):
        notes.append(
            "No pair of systems differs significantly on paired seeds; add seeds or harder layers."
        )
    normal_entries = normals
    normals = [entry[3] for entry in normals.values()]
    model_normals = [s for s in normals if not s["system_id"].startswith("baseline/")]
    if len(model_normals) < 3:
        notes.append(
            "Test at least three distinct models before judging how well this benchmark recipe separates competitors."
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
    stability = dict(available=False, reason="Two complete matched full studies required")
    if campaign.get("mode") == "full" and len(normals) >= 2 and references:
        from ..evaluation.stability import ranking_stability

        entries = list(normal_entries.items())
        specs = [entry[1][2] for entry in entries]
        if all(spec == specs[0] for spec in specs):
            try:
                stability = dict(available=True, **ranking_stability(
                    [dict(entry[0], candidate_id=sid) for sid, entry in entries], specs[0], references))
            except ValueError as error:
                stability = dict(available=False, reason=str(error))
    return dict(
        ranking_stability=stability,
        completed_studies=len(complete),
        total_studies=len(campaign["studies"]),
        effects=effects,
        metric_health=health,
        systems=systems,
        comparisons=comparisons,
        discrimination=discrimination,
        alignment=alignment,
        normalized=references is not None,
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
            raise ValueError("A research evaluation is already running")
        if (
            campaign["runtime"] != runtime_fingerprint()
            or campaign["research_runtime"] != research_fingerprint()
        ):
            raise ValueError(
                "The engine changed. Create a new evaluation; previous findings remain available."
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

                    async def run_block(manifests):
                        await execute_episodes(
                            study,
                            manifests,
                            worker,
                            lambda: self.save(campaign),
                            campaign.get("parallelism", 1),
                            "diagnostics",
                            [
                                "candidate_resolved_decisions",
                                "candidate_invalid_envelopes",
                                "candidate_rejected_operations",
                                "candidate_messages",
                                "candidate_decision_attempts",
                                "candidate_infrastructure_errors",
                                "invalid_envelopes",
                                "rejected_operations",
                                "messages",
                                "provider_attempts",
                            ],
                        )

                    if campaign.get("mode") == "pilot" and not study["system_id"].startswith(
                        "baseline/"
                    ):
                        await self.pilot(campaign, study, folder, run_block)
                    else:
                        await run_block(study["execution"]["episodes"])
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

    async def pilot(self, campaign, study, folder, run_block):
        """Add seeds in blocks; stop once the paired difference to the reference is settled."""
        rule = campaign["stopping"]
        twin = next(
            (
                s
                for s in campaign["studies"]
                if s["system_id"] == study["system_id"] and s["intervention"] is None
            ),
            study,
        )
        if twin is not study and twin.get("stopping"):

            self.trim(study, twin["stopping"]["seeds"])
            await run_block(study["execution"]["episodes"])
            return
        reference = next(
            s for s in campaign["studies"] if s["system_id"] == "baseline/" + rule["reference"]
        )
        if reference["status"] != "completed":
            raise ValueError("The reference baseline must complete before the pilot")
        seeds = sorted({m["seed"] for m in study["execution"]["episodes"]})[: rule["max_seeds"]]
        planned_looks = math.ceil(len(seeds) / rule["block_seeds"])

        family_size = planned_looks * sum(not s["system_id"].startswith("baseline/") for s in campaign["studies"])
        used, looks = [], 0
        for start in range(0, len(seeds), rule["block_seeds"]):
            block = seeds[start : start + rule["block_seeds"]]
            used.extend(block)
            await run_block([m for m in study["execution"]["episodes"] if m["seed"] in block])
            looks += 1
            keys = {case_key(m) for m in study["execution"]["episodes"] if m["seed"] in used}
            partial = dict(
                study["execution"],
                suite=dict(
                    study["execution"]["suite"],
                    cases=[c for c in study["execution"]["suite"]["cases"] if case_key(c) in keys],
                ),
                episodes=[m for m in study["execution"]["episodes"] if m["seed"] in used],
            )
            data = await asyncio.to_thread(
                collect, partial, folder / "studies" / study["id"] / "episodes"
            )
            reference_data = read_json(folder / "studies" / reference["id"] / "results.json")
            left, right, spec = common_cases(
                data, partial["suite"], reference_data, reference["execution"]["suite"]
            )
            result = compare(left, right, spec)
            se = result["paired_se"]
            av, bv = validate(left, spec), validate(right, spec)
            low, high = [100*v for v in seed_interval(
                {k: av[k] - bv[k] for k in av}, paired=True, comparisons=family_size)]
            verdict = None
            if low > rule["minimum_effect"]:
                verdict = "better_than_reference"
            elif high < rule["minimum_effect"]:
                verdict = "not_better_by_minimum_effect"
            elif (high - low) / 2 < rule["minimum_effect"] / 2:
                verdict = "precise_enough"
            study["stopping"] = dict(
                seeds=used,
                looks=looks,
                planned_looks=planned_looks,
                family_size=family_size,
                simultaneous_ci95=[low, high],
                interval_method=METHOD,
                delta=result["paired_delta"],
                paired_se=se,
                verdict=verdict,
                reference=rule["reference"],
            )
            self.save(campaign)
            if verdict is not None or len(used) >= len(seeds):
                break
        self.trim(study, used)

    @staticmethod
    def trim(study, seeds):
        execution = study["execution"]
        execution["episodes"] = [m for m in execution["episodes"] if m["seed"] in seeds]
        keys = {case_key(m) for m in execution["episodes"]}
        execution["suite"] = dict(
            execution["suite"],
            cases=[c for c in execution["suite"]["cases"] if case_key(c) in keys],
        )

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
