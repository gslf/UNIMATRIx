"""Offline design audits and family-held-out recipe compression.

The selection objective is the fewest decisions preserving a frozen reference
profile. This is a compression result, not a certificate of universal validity.
No provider code is imported or invoked by these commands.
"""

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from copy import deepcopy
from itertools import combinations, product
from pathlib import Path
from statistics import fmean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..benchmark.recipes import RecipeRepository, validate_recipe
from ..core.ids import digest
from ..evaluation.sampling import sampling_layout
from ..evaluation.scoring import case_key, domain_means, validate
from ..persistence.json_files import read_json
from ..scenarios.layers import layers_key
from .provenance import analysis_provenance

DOMAINS = tuple(f"D{i}" for i in range(1, 9))
CONDITIONS = ("standard", "stress")
ROLES = ("advantaged", "disadvantaged")


def audit_recipe(spec):
    validate_recipe(spec)
    actual = Counter((c["domain"], layers_key(c["layers"]), c["role"]) for c in spec["cases"])
    required = set(product(DOMAINS, CONDITIONS, ROLES))
    seeds = sorted({c["seed"] for c in spec["cases"]})
    shapes = [Counter((c["domain"], layers_key(c["layers"]), c["role"], c["replicate"])
                      for c in spec["cases"] if c["seed"] == seed) for seed in seeds]
    replicates = {c["replicate"] for c in spec["cases"]}
    expected = Counter({(*cell, rep): 1 for cell in required for rep in replicates})
    missing = sorted(required - set(actual))
    try:
        layout = sampling_layout({case_key(c) for c in spec["cases"]},
                                 {str(s): "fixed_peers" for s in seeds})
    except ValueError:
        layout = None
    fully_crossed = not missing and set(actual) == required
    balanced_shared = all(s == expected for s in shapes)
    complete_balanced = fully_crossed and (balanced_shared or (layout is not None and layout.kind == "independent_cell_strata"))
    return dict(recipe=spec["id"], recipe_sha256=digest(spec),
                episodes=len(spec["cases"]), ticks=spec["ticks"],
                candidate_decisions=len(spec["cases"]) * spec["ticks"],
                independent_seed_clusters=len(seeds), missing_cells=[list(c) for c in missing],
                fully_crossed=fully_crossed,
                balanced_seed_blocks=balanced_shared,
                complete_balanced_design=complete_balanced,
                sampling_design=layout.kind if layout else "unsupported",
                ranking_design_compatible=layout is not None,
                minimum_stratum_clusters=layout.minimum_clusters if layout else 0,
                inference_calls=0)


def balanced_bank(base, seeds, *, ticks=240, ident="quality-bank-v2", sampling="shared"):
    """Author complete blocks; never select seeds using model scores."""
    if (not seeds or len(set(seeds)) != len(seeds)
            or any(type(s) is not int or not 0 <= s < 2**31 for s in seeds)):
        raise ValueError("distinct_nonnegative_seed_list_required")
    if sampling not in ("shared", "independent"):
        raise ValueError("unknown_seed_allocation")
    validate_recipe(base)
    if ticks not in (72, 240):
        raise ValueError("supported_horizon_required")
    templates = {}
    for case in base["cases"]:
        key = (case["domain"], layers_key(case["layers"]))
        if key in templates and templates[key] != case["layers"]:
            raise ValueError("ambiguous_condition_template")
        templates[key] = case["layers"]
    if set(templates) != set(product(DOMAINS, CONDITIONS)):
        raise ValueError("base_must_supply_every_domain_and_condition")
    result = deepcopy(base)
    result.update(id=ident, name="Balanced quality bank",
                  description="Complete domain, condition and role blocks for offline calibration.",
                  ticks=ticks, cases=[])
    allocated = set()
    for seed, domain, condition, role in product(seeds, DOMAINS, CONDITIONS, ROLES):
        if sampling == "independent":
            seed = int(digest(["independent-cell-seed-v1", seed, domain, condition, role])[:8], 16) % 2**31
            if seed in allocated:
                raise ValueError("derived_seed_collision_use_other_block_seeds")
            allocated.add(seed)
        layers = deepcopy(templates[domain, condition])
        if isinstance(layers, dict) and ticks != base["ticks"]:
            for event in layers.get("shock", {}).get("events", []):
                when = event["when"]
                for key in ("at", "from", "to"):
                    if key in when:
                        when[key] = max(1, math.floor(when[key] * ticks / base["ticks"]))
                if "duration" in event["effect"]:
                    event["effect"]["duration"] = max(
                        1, math.floor(event["effect"]["duration"] * ticks / base["ticks"]))
        result["cases"].append(dict(domain=domain, layers=layers, seed=seed, role=role, replicate=0))
    return validate_recipe(result)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Artifact(Strict):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PanelModel(Strict):
    id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    split: Literal["development", "holdout"]
    reports: list[Artifact] = Field(min_length=2)


class CompressionContract(Strict):

    max_profile_error: float = Field(default=0.02, gt=0, lt=1)
    max_repeat_spread: float = Field(default=0.03, gt=0, lt=1)
    meaningful_rank_gap: float = Field(default=0.05, gt=0, lt=1)
    max_rank_reversal_fraction: float = Field(default=0.0, ge=0, lt=1)
    seed_order: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def seeds(self):
        if len(set(self.seed_order)) != len(self.seed_order):
            raise ValueError("duplicate_seed_order")
        return self


class CompressionPanel(Strict):
    models: list[PanelModel] = Field(min_length=12)
    contract: CompressionContract

    @model_validator(mode="after")
    def split(self):
        if len({m.id for m in self.models}) != len(self.models):
            raise ValueError("duplicate_model")
        dev = {m.family for m in self.models if m.split == "development"}
        hold = {m.family for m in self.models if m.split == "holdout"}
        if dev & hold or len(dev | hold) < 4 or len(dev) < 2 or len(hold) < 2:
            raise ValueError("disjoint_model_families_required")
        if any(sum(m.split == split for m in self.models) < 6 for split in ("development", "holdout")):
            raise ValueError("six_models_per_split_required")
        return self


def read_reports(panel, root):
    root = Path(root).resolve()
    reports, identities, hashes, run_ids = {}, set(), set(), set()
    configurations = set()
    suite = None
    execution_runtime = None
    for model in panel.models:
        runs = []
        model_identity = None
        configuration = None
        execution_configuration = None
        repeat_ids = set()
        for artifact in model.reports:
            path = (root / artifact.path).resolve()
            if Path(artifact.path).is_absolute() or not path.is_relative_to(root):
                raise ValueError("report_path_outside_artifact_root")
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha != artifact.sha256 or sha in hashes:
                raise ValueError("altered_or_reused_report")
            hashes.add(sha)
            report = json.loads(data)
            measurement = report["validation_measurement"]
            run_id = measurement["run_id"]
            if (not isinstance(run_id, str) or not run_id or run_id in run_ids
                    or type(measurement["repeat"]) is not int or measurement["repeat"] < 0
                    or measurement["repeat"] in repeat_ids):
                raise ValueError("independent_run_ids_required")
            if measurement["model"] != model.id:
                raise ValueError("measurement_model_mismatch")
            run_ids.add(run_id)
            repeat_ids.add(measurement["repeat"])
            current_configuration = report["model_configuration_sha256"]
            if not isinstance(current_configuration, str) or not re.fullmatch(r"[a-f0-9]{64}", current_configuration):
                raise ValueError("model_configuration_hash_required")
            if configuration is None:
                configuration = current_configuration
            elif configuration != current_configuration:
                raise ValueError("model_configuration_changed_across_repeats")
            current_execution = report.get("execution_configuration")
            if (not isinstance(current_execution, dict)
                    or set(current_execution) != {"runtime", "parallelism"}
                    or not isinstance(current_execution["runtime"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", current_execution["runtime"])
                    or type(current_execution["parallelism"]) is not int
                    or not 1 <= current_execution["parallelism"] <= 64):
                raise ValueError("execution_configuration_required")
            if report.get("execution_configuration_sha256") != digest(current_execution):
                raise ValueError("execution_configuration_hash_mismatch")
            if execution_configuration is None:
                execution_configuration = current_execution
            elif execution_configuration != current_execution:
                raise ValueError("execution_configuration_changed_across_repeats")
            if execution_runtime is None:
                execution_runtime = current_execution["runtime"]
            elif execution_runtime != current_execution["runtime"]:
                raise ValueError("execution_runtime_mismatch_across_panel")
            spec = report["suite"]
            if suite is None:
                suite = spec
            elif digest(spec) != digest(suite):
                raise ValueError("same_frozen_reference_required")
            results = report["results"]
            if results["data_kind"] != "empirical":
                raise ValueError("empirical_reports_required")
            identity = results["candidate_id"]
            if model_identity is None:
                model_identity = identity
            elif identity != model_identity:
                raise ValueError("model_identity_changed_across_repeats")
            runs.append(validate(results, spec))
        if model_identity in identities or configuration in configurations:
            raise ValueError("duplicate_model_configuration")
        identities.add(model_identity)
        configurations.add(configuration)
        reports[model.id] = runs
    return suite, reports


def compression_metrics(names, reports, suite, seeds, contract):
    means, full_means, errors, spreads, full_spreads = {}, {}, [], [], []
    selected = set(seeds)
    for name in names:
        full = [domain_means(r, suite) for r in reports[name]]
        short = [domain_means({k: v for k, v in r.items() if k[2] in selected}, suite)
                 for r in reports[name]]
        for a, b in zip(short, full):
            errors.extend(abs(a[d] - b[d]) for d in DOMAINS)
        spreads.extend(max(r[d] for r in short) - min(r[d] for r in short) for d in DOMAINS)
        full_spreads.extend(max(r[d] for r in full) - min(r[d] for r in full) for d in DOMAINS)
        means[name] = {d: fmean(r[d] for r in short) for d in DOMAINS}
        full_means[name] = {d: fmean(r[d] for r in full) for d in DOMAINS}
    by_domain = {d: dict(informative_pairs=0, reversals=0) for d in DOMAINS}
    for left, right in combinations(names, 2):
        for domain in DOMAINS:
            full_delta = full_means[left][domain] - full_means[right][domain]
            if abs(full_delta) >= contract.meaningful_rank_gap:
                by_domain[domain]["informative_pairs"] += 1
                short_delta = means[left][domain] - means[right][domain]


                by_domain[domain]["reversals"] += full_delta * short_delta <= 0
    for row in by_domain.values():
        row["rank_reversal_fraction"] = (row["reversals"] / row["informative_pairs"]
                                         if row["informative_pairs"] else None)
    informative = sum(row["informative_pairs"] for row in by_domain.values())
    reversals = sum(row["reversals"] for row in by_domain.values())
    fraction = reversals / informative if informative else None
    checks = dict(profile_error=max(errors) <= contract.max_profile_error,
                  repeatability=max(spreads) <= contract.max_repeat_spread,
                  reference_repeatability=max(full_spreads) <= contract.max_repeat_spread,
                  domain_resolution=all(row["informative_pairs"] > 0 for row in by_domain.values()),
                  ranking=all(row["rank_reversal_fraction"] is not None and
                              row["rank_reversal_fraction"] <= contract.max_rank_reversal_fraction
                              for row in by_domain.values()))
    return dict(max_profile_error=max(errors), max_repeat_spread=max(spreads),
                reference_max_repeat_spread=max(full_spreads), domain_rank_preservation=by_domain,
                informative_domain_pairs=informative, rank_reversal_fraction=fraction,
                checks=checks, passed=all(checks.values()))


def calibrate(panel, root):
    suite, reports = read_reports(panel, root)
    from ..benchmark.recipes import recipe_from_suite

    recipe = recipe_from_suite(suite)
    coverage = audit_recipe(recipe)
    if not coverage["complete_balanced_design"]:
        raise ValueError("complete_balanced_reference_required")
    layout = sampling_layout({case_key(c) for c in recipe["cases"]}, suite["population_by_seed"])
    prefix_sizes = list(layout.complete_prefixes(panel.contract.seed_order))
    dev = [m.id for m in panel.models if m.split == "development"]
    hold = [m.id for m in panel.models if m.split == "holdout"]
    trials, selected, holdout = [], None, None
    for size in prefix_sizes:
        seeds = panel.contract.seed_order[:size]
        result = compression_metrics(dev, reports, suite, seeds, panel.contract)
        trials.append(dict(seeds=seeds, **result))
        if result["passed"]:
            selected = seeds

            holdout = compression_metrics(hold, reports, suite, seeds, panel.contract)
            break
    passed = bool(holdout and holdout["passed"])
    cases = [c for c in recipe["cases"] if selected is not None and c["seed"] in selected]
    provenance = analysis_provenance("unimatrix.research.measurement_quality.calibrate")
    return dict(format="unimatrix.compression-calibration.v4", panel_sha256=digest(panel.model_dump()),
                sampling_design=layout.kind, eligible_prefix_sizes=prefix_sizes,
                analysis_provenance=provenance, analysis_sha256=provenance["sha256"],
                primary_module_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                contract=panel.contract.model_dump(), reference_recipe_sha256=digest(recipe),
                status="holdout_passed" if passed else "holdout_failed" if holdout else "no_candidate",
                selected_seeds=selected, candidate_decisions=len(cases) * recipe["ticks"] if selected else None,
                development_trials=trials, holdout=holdout, inference_calls=0,
                shortest_scope="Complete stratum-balanced prefixes in the frozen seed order at the reference horizon.",
                uncertainty_scope="Observed profile preservation on this finite family-held-out panel.",
                benchmark_superiority_demonstrated=False,
                duration_seconds=None,
                duration_scope="Measure runtime separately; fewer decisions is not an observed duration.")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="unimatrix quality")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--recipes-dir", default="config/recipes")
    audit.add_argument("--output", required=True)
    bank = sub.add_parser("bank")
    bank.add_argument("--base", default="standard-v1")
    bank.add_argument("--seeds", required=True, type=int, nargs="+")
    bank.add_argument("--ticks", type=int, choices=[72, 240], default=240)
    bank.add_argument("--sampling", choices=["shared", "independent"], default="shared",
                      help="Share each input seed across cells, or derive a distinct world seed per cell")
    bank.add_argument("--output", required=True)
    design = sub.add_parser("design")
    source = design.add_mutually_exclusive_group(required=True)
    source.add_argument("--recipe", help="Path to an authored recipe JSON")
    source.add_argument("--base", help="Bundled recipe ID")
    design.add_argument("--systems", type=int, default=2)
    design.add_argument("--target-gap", type=float, default=0.05)
    design.add_argument("--seconds-per-decision", type=float)
    design.add_argument("--output", required=True)
    calibration = sub.add_parser("calibrate")
    calibration.add_argument("--panel", required=True)
    calibration.add_argument("--artifact-root", required=True)
    calibration.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            result = dict(recipes=[audit_recipe(r) for r in RecipeRepository(args.recipes_dir).all().values()], inference_calls=0)
        elif args.command == "bank":
            result = balanced_bank(RecipeRepository().get(args.base), args.seeds, ticks=args.ticks, sampling=args.sampling)
        elif args.command == "design":
            from .resolution import resolution_plan

            recipe = read_json(args.recipe) if args.recipe else RecipeRepository().get(args.base)
            result = resolution_plan(recipe, systems=args.systems, target_gap=args.target_gap,
                                     seconds_per_decision=args.seconds_per_decision)
        else:
            result = calibrate(CompressionPanel.model_validate(read_json(args.panel)), args.artifact_root)
        with Path(args.output).open("x") as file:
            json.dump(result, file, indent=2, allow_nan=False)
            file.write("\n")
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(dict(output=args.output, inference_calls=0)))
    if args.command == "design" and not result["preflight_passed"]:
        return 2
    return 2 if args.command == "calibrate" and result["status"] != "holdout_passed" else 0
