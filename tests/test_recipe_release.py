"""Release invariants for bundled panels and defects found during calibration."""

import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from unimatrix.benchmark.recipes import BUNDLED, RecipeRepository, bind_candidate
from unimatrix.benchmark.scheduler import run_episode
from unimatrix.scenarios.layers import layers_key, resolve_layers


def test_bundled_recipe_catalog_and_peer_design():
    assert {p.stem for p in BUNDLED.glob("*.json")} == {"standard-v1", "validation-v1"}
    for spec in RecipeRepository().all().values():
        assert len(spec["peers"]) == 7 and all(
            isinstance(p, dict) and p["policy"] != "oracle" and p["role"] for p in spec["peers"]
        )
        stress = resolve_layers(next(c["layers"] for c in spec["cases"] if layers_key(c["layers"]) == "stress"))
        assert stress["noise"]["source_drift"] and stress["shock"]["task_fault"]
        assert stress["society"]["adversarial_share"] == 0


@pytest.mark.asyncio
async def test_inactive_candidate_cannot_live_off_peers_commons_score(tmp_path):
    execution = bind_candidate(RecipeRepository().get("standard-v1"), "passive")
    manifest = next(m for m in execution["episodes"] if m["domain"] == "D4")
    result = await run_episode(manifest, tmp_path)
    assert result["completed_tick"] == manifest["ticks"]
    assert all(m["normalized_value"] == 0 for m in result["metrics"].values())
    assert all(m["attribution"] == "focal_and_collective" for m in result["metrics"].values())


@pytest.mark.asyncio
async def test_oracle_negotiates_with_the_actual_allocation_partner(tmp_path):
    execution = bind_candidate(RecipeRepository().get("standard-v1"), "oracle")
    manifest = next(m for m in execution["episodes"] if m["domain"] == "D6")
    result = await run_episode(manifest, tmp_path)
    assert result["metrics"]["D6.execution"]["normalized_value"] == 1
    assert result["metrics"]["D6.mandate_service"]["normalized_value"] > 0.9


@pytest.mark.skipif(
    not os.environ.get("UNIMATRIX_DIST_DIR"), reason="Run after building release artifacts"
)
def test_built_artifacts_exclude_private_config_and_extra_recipes():
    directory = Path(os.environ["UNIMATRIX_DIST_DIR"])
    artifacts = [*directory.glob("*.whl"), *directory.glob("*.tar.gz")]
    assert len(artifacts) == 2
    for artifact in artifacts:
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                entries = {
                    name: archive.read(name)
                    for name in archive.namelist()
                    if not name.endswith("/")
                }
        else:
            with tarfile.open(artifact) as archive:
                entries = {
                    m.name.split("/", 1)[1]: archive.extractfile(m).read()
                    for m in archive
                    if m.isfile()
                }
        assert not any(name.startswith(("tests/", "docs/", "internal/")) for name in entries)
        assert "CHANGELOG.md" not in entries
        assert not any(name.endswith("/benchmark/plans.py") for name in entries)
        assert not any(name.startswith("config/") for name in entries)
        assert not any(
            name.endswith(("-credentials.json", ".pem", ".key", "/.env", ".gguf", ".safetensors"))
            for name in entries
        )
        recipes = {
            Path(name).name
            for name in entries
            if "/benchmark/plans/" in name and name.endswith(".json")
        }
        assert recipes == {"standard-v1.json", "validation-v1.json"}
        assert not any(name.startswith("internal/") for name in entries)
        for name, data in entries.items():
            if "/benchmark/plans/" in name and name.endswith(".json"):
                assert json.loads(data) == RecipeRepository().get(Path(name).stem)
        assert not any(name.endswith("/benchmark/core-v1.json") for name in entries)
        assert not any(
            b"distribution-private-sentinel" in data
            for name, data in entries.items()
            if not name.endswith("test_recipe_release.py")
        )


@pytest.mark.asyncio
async def test_stress_market_with_low_quality_still_has_a_working_reference(tmp_path):
    spec = RecipeRepository().get("standard-v1")

    for case in spec["cases"]:
        if case["domain"] == "D2":
            case["seed"] = 103
            if layers_key(case["layers"]) == "stress":
                case["role"] = "disadvantaged"
    scores = {}
    for system in ("passive", "oracle"):
        manifest = next(
            m
            for m in bind_candidate(spec, system)["episodes"]
            if m["domain"] == "D2"
            and m["seed"] == 103
            and m["role"] == "disadvantaged"
            and layers_key(m["layers"]) == "stress"
        )
        result = await run_episode(manifest, tmp_path / system)
        scores[system] = result["metrics"]
    assert scores["oracle"]["D2.delivery"]["normalized_value"] == 1
    assert scores["oracle"]["D2.joint_gain"]["normalized_value"] > 0.9
    assert (
        scores["oracle"]["D2.private_gain"]["normalized_value"]
        > scores["passive"]["D2.private_gain"]["normalized_value"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["D3", "D8"])
async def test_bundled_workers_cannot_replace_an_inactive_coordinator(tmp_path, domain):
    spec = RecipeRepository().get("standard-v1")
    execution = bind_candidate(spec, "passive")
    for condition in ("standard", "stress"):
        manifest = next(
            m
            for m in execution["episodes"]
            if m["domain"] == domain and layers_key(m["layers"]) == condition
        )
        result = await run_episode(manifest, tmp_path)
        assert result["completed_tick"] == manifest["ticks"]
        assert all(m["normalized_value"] == 0 for m in result["metrics"].values())
