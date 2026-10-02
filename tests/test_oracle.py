"""The oracle is a research-only ceiling that follows the feasibility witness."""

import pytest

from unimatrix.benchmark.manifests import episode
from unimatrix.benchmark.recipes import RecipeRepository, validate_recipe
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.benchmark.validation import validate_policy


async def test_oracle_reaches_the_certified_ceiling(tmp_path):
    result = await run_episode(episode("D1", candidate="oracle"), tmp_path)
    assert result["completed_tick"] == 240
    assert result["metrics"]["D1.decision"]["normalized_value"] == 1
    assert result["metrics"]["D1.prediction"]["normalized_value"] == 1


def test_oracle_never_enters_recipes_or_leaderboards():
    validate_policy("oracle")
    spec = RecipeRepository().get("standard-v1")
    spec["peers"][0] = "oracle"
    with pytest.raises(ValueError, match="research reference"):
        validate_recipe(spec)


async def test_references_are_computed_once_per_revision(tmp_path):
    from unimatrix.benchmark.recipes import bind_candidate
    from unimatrix.benchmark.scheduler import collect
    from unimatrix.evaluation.references import (
        CEILINGS,
        FLOORS,
        ensure_references,
        reference_folder,
    )
    from unimatrix.evaluation.scoring import validate

    spec = RecipeRepository().get("standard-v1")
    spec["cases"] = [next(c for c in spec["cases"] if c["domain"] == d) for d in ("D1", "D5")]
    spec["domains"] = {d: v for d, v in spec["domains"].items() if d in {"D1", "D5"}}
    execution = bind_candidate(spec, "passive")
    references = await ensure_references(spec, tmp_path, execution, parallelism=2)
    assert len(references) == 2
    assert all(0 <= floor <= ceiling <= 1 for floor, ceiling in references.values())
    folder = reference_folder(tmp_path, execution)
    stamp = {p: p.stat().st_mtime for p in folder.rglob("episode.db")}
    assert len(stamp) == 2 * len(FLOORS + CEILINGS)
    passive = bind_candidate(spec, "passive")
    passive_scores = validate(collect(passive, folder / "passive" / "episodes"), passive["suite"])
    assert all(floor == passive_scores[key] for key, (floor, _) in references.items())
    assert not (folder / "random").exists()
    again = await ensure_references(spec, tmp_path, execution)
    assert again == references
    assert {p: p.stat().st_mtime for p in folder.rglob("episode.db")} == stamp


async def test_oracle_bargains_in_d2(tmp_path):
    gain = "D2.private_gain"
    for role, better in [("disadvantaged", True), ("advantaged", False)]:
        reciprocal = await run_episode(
            episode("D2", role=role, candidate="reciprocal"), tmp_path / role / "r"
        )
        oracle = await run_episode(episode("D2", role=role, candidate="oracle"), tmp_path / role / "o")
        own, theirs = (
            oracle["metrics"][gain]["normalized_value"],
            reciprocal["metrics"][gain]["normalized_value"],
        )
        assert own > theirs if better else own >= theirs, (role, own, theirs)
        assert oracle["metrics"]["D2.delivery"]["normalized_value"] == 1
        assert reciprocal["metrics"]["D2.delivery"]["normalized_value"] > 0


def test_d4_reserve_target_follows_the_layer():
    from unimatrix.scenarios import get_scenario

    scenario = get_scenario("D4")
    for preset, share in [("lenient", 50), ("standard", 90)]:
        state = scenario.build(episode("D4", layers=preset))
        events = scenario.evolve(state, state)
        snapshot = next(e for e in events if e["type"] == "stock_snapshot")
        assert snapshot["payload"]["reserve_target"] == 10000 * 8 * share // 100
        assert scenario.feasible(episode("D4", layers=preset))["valid"]
