import json

import httpx
import pytest

from unimatrix.benchmark.plans import PlanRepository, bind_candidate
from unimatrix.benchmark.recipes import RecipeRepository
from unimatrix.main import cli
from unimatrix.persistence.json_files import write_json
from unimatrix.web.server import build_app


def test_recipe_repository_preserves_legacy_bindings_and_directory(tmp_path, monkeypatch):
    original = PlanRepository().get("compact-v1")
    custom = dict(original, id="authored-v1", name="Authored v1")
    write_json(tmp_path / "config" / "plans" / "authored.json", custom)
    monkeypatch.chdir(tmp_path)
    recipes = RecipeRepository()
    assert recipes.get("authored-v1") == custom
    assert bind_candidate(recipes.get("compact-v1"), "passive") == bind_candidate(
        original, "passive"
    )


def test_recipe_cli_names_and_legacy_aliases(capsys):
    assert cli(["recipes"]) == 0
    current = json.loads(capsys.readouterr().out)
    assert cli(["plans"]) == 0
    assert json.loads(capsys.readouterr().out) == current
    for command in ["serve", "run"]:
        with pytest.raises(SystemExit) as exit:
            cli([command, "--help"])
        assert exit.value.code == 0
        help_text = capsys.readouterr().out
        assert "--recipes-dir" in help_text
        assert ("--default-recipe" if command == "serve" else "--recipe") in help_text
        assert "--plan" not in help_text


@pytest.mark.asyncio
async def test_recipe_api_and_lab_keep_saved_json_compatible(tmp_path):
    app = build_app(tmp_path / "runs", tmp_path / "models", tmp_path / "recipes")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/recipe-lab")
        assert page.status_code == 200
        assert "Recipe lab" in page.text and "Draft a benchmark recipe" in page.text
        assert "Plan lab" not in page.text
        assert (await client.get("/research")).headers["location"] == "/recipe-lab"
        for asset in ["recipe-lab.html", "recipe-lab.js", "recipe-lab.css"]:
            assert (await client.get("/static/" + asset)).status_code == 200
        recipes = (await client.get("/api/recipes")).json()
        assert recipes == (await client.get("/api/plans")).json()
        assert all(r["recipe_hash"] == r["plan_hash"] for r in recipes)
        options = (await client.get("/api/recipe-lab/options")).json()
        assert options == (await client.get("/api/research/options")).json()
        payload = dict(
            base_recipe="compact-v1",
            id="research-v1",
            name="Research v1",
            description="Benchmark recipe test",
            domains=["D1"],
            seeds=[100],
            roles=["advantaged"],
        )
        response = await client.post("/api/recipe-lab/drafts", json=payload)
        assert response.status_code == 200, response.text
        draft = response.json()
        assert draft["recipe"] == draft["plan"]
        assert draft["design"]["base_recipe"] == "compact-v1"
        payload["base_plan"] = payload.pop("base_recipe")
        older = (await client.post("/api/research/drafts", json=payload)).json()
        assert older["recipe_hash"] == draft["recipe_hash"]
        restored = (await client.get("/api/recipe-lab/drafts/" + older["id"])).json()
        assert restored["recipe"] == draft["recipe"]
        exported = await client.get(f"/api/recipe-lab/drafts/{draft['id']}/export")
        assert exported.json() == draft["recipe"]
        schema = (await client.get("/openapi.json")).json()
        assert "/api/recipes" in schema["paths"] and "/api/plans" not in schema["paths"]
        assert "/api/recipe-lab/drafts" in schema["paths"]
        assert "/api/research/drafts" not in schema["paths"]
