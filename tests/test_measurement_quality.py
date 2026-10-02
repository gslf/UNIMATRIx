import hashlib
import json
from copy import deepcopy

import pytest

from unimatrix.benchmark.recipes import RecipeRepository, scoring_spec
from unimatrix.core.ids import digest
from unimatrix.research.measurement_quality import (
    CompressionPanel,
    audit_recipe,
    balanced_bank,
    calibrate,
)


def test_coverage_exposes_confounding_and_balanced_blocks_fix_it():
    base = RecipeRepository().get("standard-v1")
    original = deepcopy(base)
    old = audit_recipe(base)
    assert len(old["missing_cells"]) == 16
    assert not old["fully_crossed"] and not old["balanced_seed_blocks"]
    bank = balanced_bank(base, [701, 702], ticks=240)
    audit = audit_recipe(bank)
    assert audit["fully_crossed"] and audit["balanced_seed_blocks"]
    assert audit["episodes"] == 64 and audit["candidate_decisions"] == 15360
    assert base == original
    events = [e for c in bank["cases"] if isinstance(c["layers"], dict)
              for e in c["layers"]["shock"]["events"]]
    assert events and all(120 <= e["when"]["at"] < 240 for e in events if "at" in e["when"])
    assert {e["effect"]["duration"] for e in events if "duration" in e["effect"]} == {10, 20}
    assert all(140 <= e["when"]["from"] < e["when"]["to"] <= 200
               for e in events if "from" in e["when"])


@pytest.mark.parametrize("seeds", [[], [1, 1], [-1], [True], [2**31]])
def test_invalid_seeds_cannot_author_a_bank(seeds):
    with pytest.raises(ValueError):
        balanced_bank(RecipeRepository().get("standard-v1"), seeds)


@pytest.fixture
def panel(tmp_path):
    bank = balanced_bank(RecipeRepository().get("standard-v1"), [701, 702, 703])
    suite = scoring_spec(bank)
    rows = []
    for i in range(12):
        split = "development" if i < 6 else "holdout"
        model = dict(id=f"model-{i}", family=f"family-{i // 3}", split=split, reports=[])
        for repeat in range(2):
            results = dict(candidate_id=f"configuration-{i}", data_kind="empirical",
                           benchmark_id=suite["benchmark_id"], scaffold_id=suite["scaffold_id"],
                           suite_hash=digest(suite), budget_track="accounted_compute", runs=[])
            for case in bank["cases"]:
                value = 0.1 + 0.06 * i + {701: 0.04, 702: -0.04, 703: 0}[case["seed"]]
                results["runs"].append(dict(case, status="completed", population="P0",
                                           scores={k: value for k in suite["domains"][case["domain"]]["metrics"]}))
            execution = dict(runtime=digest("fixed-evaluator"), parallelism=1)
            report = dict(suite=suite, results=results, model_configuration_sha256=digest(i),
                          execution_configuration=execution, execution_configuration_sha256=digest(execution),
                          validation_measurement=dict(model=model["id"], run_id=f"run-{i}-{repeat}", repeat=repeat))
            path = tmp_path / f"{i}-{repeat}.json"
            path.write_text(json.dumps(report))
            model["reports"].append(dict(path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        rows.append(model)
    return dict(models=rows, contract=dict(seed_order=[701, 702, 703]))


def rewrite(tmp_path, model, fn):
    for artifact in model["reports"]:
        path = tmp_path / artifact["path"]
        data = json.loads(path.read_text())
        fn(data)
        path.write_text(json.dumps(data))
        artifact["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_shortest_development_prefix_is_checked_once_on_heldout_families(panel, tmp_path):
    result = calibrate(CompressionPanel.model_validate(panel), tmp_path)
    assert result["status"] == "holdout_passed"
    assert result["selected_seeds"] == [701, 702]
    assert len(result["development_trials"]) == 2
    assert result["candidate_decisions"] == 15360
    assert result["benchmark_superiority_demonstrated"] is False
    assert result["duration_seconds"] is None and result["inference_calls"] == 0


def test_failed_holdout_never_tunes_a_longer_prefix(panel, tmp_path):
    for model in panel["models"][6:]:
        def perturb(data):
            for row in data["results"]["runs"]:
                for metric in row["scores"]:
                    row["scores"][metric] += {701: 0.04, 702: 0.04, 703: -0.08}[row["seed"]]
        rewrite(tmp_path, model, perturb)
    result = calibrate(CompressionPanel.model_validate(panel), tmp_path)
    assert result["status"] == "holdout_failed"
    assert result["selected_seeds"] == [701, 702]
    assert len(result["development_trials"]) == 2


def test_saturated_reference_does_not_pass_by_matching_it(panel, tmp_path):
    for model in panel["models"]:
        def saturate(data):
            for row in data["results"]["runs"]:
                row["scores"] = dict.fromkeys(row["scores"], 0.0)
        rewrite(tmp_path, model, saturate)
    result = calibrate(CompressionPanel.model_validate(panel), tmp_path)
    assert result["status"] == "no_candidate"


@pytest.mark.parametrize("split", ["development", "holdout"])
def test_one_saturated_domain_cannot_hide_behind_other_domains(panel, tmp_path, split):
    for model in panel["models"]:
        if model["split"] != split:
            continue
        def saturate_one(data):
            for row in data["results"]["runs"]:
                if row["domain"] == "D7":
                    row["scores"] = dict.fromkeys(row["scores"], 0.0)
        rewrite(tmp_path, model, saturate_one)
    result = calibrate(CompressionPanel.model_validate(panel), tmp_path)
    assert result["status"] == ("no_candidate" if split == "development" else "holdout_failed")
    checks = result["development_trials"][-1] if split == "development" else result["holdout"]
    assert checks["informative_domain_pairs"] > 0
    assert checks["domain_rank_preservation"]["D7"]["informative_pairs"] == 0
    assert not checks["checks"]["domain_resolution"]


def test_unstable_full_reference_cannot_certify_a_stable_prefix(panel, tmp_path):
    for model in panel["models"]:
        def perturb_reference(data):
            sign = 1 if data["validation_measurement"]["repeat"] else -1
            for row in data["results"]["runs"]:
                if row["seed"] == 703:
                    row["scores"] = {k: v + sign * 0.057 for k, v in row["scores"].items()}
        rewrite(tmp_path, model, perturb_reference)
    result = calibrate(CompressionPanel.model_validate(panel), tmp_path)
    prefix = result["development_trials"][1]
    assert prefix["checks"]["profile_error"] and prefix["checks"]["repeatability"]
    assert prefix["reference_max_repeat_spread"] == pytest.approx(0.038)
    assert not prefix["checks"]["reference_repeatability"]
    assert result["status"] == "no_candidate"


def test_rank_reversal_tolerance_applies_separately_to_each_domain(panel, tmp_path):
    panel["contract"].update(max_profile_error=0.21, max_rank_reversal_fraction=0.2)
    for index, model in enumerate(panel["models"]):
        def reverse_one(data):
            for row in data["results"]["runs"]:
                value = 0.35 + 0.06 * (index % 6)
                if row["domain"] == "D7":
                    value = 0.65 - 0.06 * (index % 6) if row["seed"] == 701 else value
                row["scores"] = dict.fromkeys(row["scores"], value)
        rewrite(tmp_path, model, reverse_one)
    result = calibrate(CompressionPanel.model_validate(panel), tmp_path)
    first = result["development_trials"][0]
    assert first["checks"]["profile_error"]
    assert first["rank_reversal_fraction"] < 0.2
    assert first["domain_rank_preservation"]["D7"]["rank_reversal_fraction"] == 1
    assert not first["checks"]["ranking"]
    assert result["selected_seeds"] != [701]


@pytest.mark.parametrize("fault", ["family_leak", "synthetic", "tamper", "reused_run", "reused_identity", "reused_configuration", "invalid_repeat", "incomplete"])
def test_invalid_empirical_panel_is_rejected(panel, tmp_path, fault):
    model = panel["models"][0]
    if fault == "family_leak":
        panel["models"][6]["family"] = model["family"]
    elif fault == "synthetic":
        rewrite(tmp_path, model, lambda d: d["results"].update(data_kind="synthetic"))
    elif fault == "tamper":
        (tmp_path / model["reports"][0]["path"]).write_text("{}")
    elif fault == "reused_run":
        rewrite(tmp_path, model, lambda d: d["validation_measurement"].update(run_id="same-run"))
    elif fault == "reused_identity":
        rewrite(tmp_path, model, lambda d: d["results"].update(candidate_id="configuration-1"))
    elif fault == "reused_configuration":
        rewrite(tmp_path, model, lambda d: d.update(model_configuration_sha256=digest(1)))
    elif fault == "invalid_repeat":
        rewrite(tmp_path, model, lambda d: d["validation_measurement"].update(repeat=True))
    else:
        rewrite(tmp_path, model, lambda d: d["results"]["runs"].pop())
    with pytest.raises(ValueError):
        calibrate(CompressionPanel.model_validate(panel), tmp_path)


def independent_panel(panel, tmp_path):
    bank = balanced_bank(RecipeRepository().get("standard-v1"), [701, 702, 703], sampling="independent")
    suite = scoring_spec(bank)
    for model in panel["models"]:
        def remap(data):
            for row, case in zip(data["results"]["runs"], bank["cases"]):
                row.update(case)
            data["suite"] = suite
            data["results"]["suite_hash"] = digest(suite)
        rewrite(tmp_path, model, remap)
    panel["contract"]["seed_order"] = [c["seed"] for c in bank["cases"]]
    return panel


def test_independent_compression_considers_only_complete_balanced_blocks(panel, tmp_path):
    panel = independent_panel(panel, tmp_path)
    result = calibrate(CompressionPanel.model_validate(panel), tmp_path)
    assert result["status"] == "holdout_passed"
    assert result["sampling_design"] == "independent_cell_strata"
    assert result["eligible_prefix_sizes"] == [32, 64, 96]
    assert result["selected_seeds"] == panel["contract"]["seed_order"][:64]
    assert len(result["development_trials"]) == 2
    assert result["candidate_decisions"] == 15360


def test_independent_failed_holdout_does_not_try_another_prefix(panel, tmp_path):
    for model in panel["models"][6:]:
        def perturb(data):
            for row in data["results"]["runs"]:
                row["scores"] = {k: v + {701: .04, 702: .04, 703: -.08}[row["seed"]]
                                 for k, v in row["scores"].items()}
        rewrite(tmp_path, model, perturb)
    panel = independent_panel(panel, tmp_path)
    result = calibrate(CompressionPanel.model_validate(panel), tmp_path)
    assert result["status"] == "holdout_failed"
    assert len(result["development_trials"]) == 2
    assert result["selected_seeds"] == panel["contract"]["seed_order"][:64]


@pytest.mark.parametrize("fault", ["missing", "hash", "boolean", "range", "runtime_format",
                                   "changed_parallel", "changed_runtime", "cross_model_runtime"])
def test_execution_conditions_are_required_for_replica_qualification(panel, tmp_path, fault):
    model = panel["models"][1 if fault == "cross_model_runtime" else 0]

    def change(report):
        if fault == "missing":
            report.pop("execution_configuration")
            report.pop("execution_configuration_sha256")
            return
        execution = report["execution_configuration"]
        if fault == "hash":
            execution["parallelism"] = 4
            return
        if fault == "boolean":
            execution["parallelism"] = True
        elif fault == "range":
            execution["parallelism"] = 65
        elif fault == "runtime_format":
            execution["runtime"] = "not-a-runtime-hash"
        elif fault == "changed_parallel" and report["validation_measurement"]["repeat"] == 1:
            execution["parallelism"] = 4
        elif fault == "changed_runtime" and report["validation_measurement"]["repeat"] == 1:
            execution["runtime"] = digest("other-evaluator")
        elif fault == "cross_model_runtime":
            execution["runtime"] = digest("other-evaluator")
        report["execution_configuration_sha256"] = digest(execution)

    rewrite(tmp_path, model, change)
    with pytest.raises(ValueError, match="execution"):
        calibrate(CompressionPanel.model_validate(panel), tmp_path)


def test_declared_model_specific_parallelism_can_remain_stable_across_repeats(panel, tmp_path):
    def change(report):
        report["execution_configuration"]["parallelism"] = 4
        report["execution_configuration_sha256"] = digest(report["execution_configuration"])

    rewrite(tmp_path, panel["models"][1], change)
    result = calibrate(CompressionPanel.model_validate(panel), tmp_path)
    assert result["status"] == "holdout_passed"
