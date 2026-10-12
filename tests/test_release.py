"""Release invariants: the bundled recipes only, no private data, no run data in artifacts."""

import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from unimatrix.polis.recipe import BUNDLED, PolisRecipes, normalize

ROOT = Path(__file__).resolve().parents[1]


BUNDLED_RECIPES = ["standard-v2", "hell-v1"]


def test_only_the_default_recipes_are_bundled():
    assert sorted(p.stem for p in BUNDLED.glob("*.json")) == sorted(BUNDLED_RECIPES)
    assert list(PolisRecipes(None).all()) == BUNDLED_RECIPES


def test_repository_ships_no_run_data():
    explorer = ROOT / "explorer"
    assert not (explorer / "demo").exists()
    assert not any(explorer.rglob("*.xcdn")) or all(
        p.is_relative_to(explorer / "data") for p in explorer.rglob("*.xcdn"))


@pytest.mark.skipif(not os.environ.get("UNIMATRIX_DIST_DIR"), reason="Run after building release artifacts")
def test_built_artifacts_exclude_private_config_and_extra_recipes():
    directory = Path(os.environ["UNIMATRIX_DIST_DIR"])
    artifacts = [*directory.glob("*.whl"), *directory.glob("*.tar.gz")]
    assert len(artifacts) == 2
    for artifact in artifacts:
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                entries = {n: archive.read(n) for n in archive.namelist() if not n.endswith("/")}
        else:
            with tarfile.open(artifact) as archive:
                entries = {m.name.split("/", 1)[1]: archive.extractfile(m).read() for m in archive if m.isfile()}
        assert not any(n.startswith(("tests/", "docs/", "internal/", "config/", "runs/", "explorer/"))
                       for n in entries)
        assert not any(n.endswith(("-credentials.json", ".pem", ".key", "/.env", ".gguf", ".safetensors", ".xcdn"))
                       for n in entries)
        recipes = {n: d for n, d in entries.items() if "/polis/recipes/" in n}
        assert {Path(n).stem for n in recipes} == set(BUNDLED_RECIPES)
        for name, data in recipes.items():
            assert normalize(json.loads(data)) == PolisRecipes(None).get(Path(name).stem)
        assert any(n.endswith("/models/personalities.json") for n in entries)
        assert not any(b"distribution-private-sentinel" in d for d in entries.values())
