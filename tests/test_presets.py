"""Persona, society and template presets: valid, deterministic and usable end to end."""

import httpx
import pytest
from fastapi import FastAPI

from tests.test_research import MODEL
from unimatrix.benchmark.personas import (
    apply_persona,
    catalog,
    render,
    societies,
    templates,
    validate_persona,
)
from unimatrix.benchmark.recipes import RecipeRepository
from unimatrix.benchmark.validation import validate_policy
from unimatrix.research.campaigns import persona_alignment
from unimatrix.research.design import Design, build_design
from unimatrix.web.benchmark import build_router
from unimatrix.web.research import build_research_router


def test_catalogs_are_valid_and_render_deterministically():
    personas = catalog()
    assert len(personas) >= 12 and "neutral" in personas
    for persona in personas.values():
        validate_persona({k: v for k, v in persona.items() if k not in {"id", "system_prompt"}})
        assert persona["system_prompt"] == render(persona)
        assert 80 <= len(persona["system_prompt"].split()) <= 220
        assert "neuroticism score is" in persona["system_prompt"]
        assert " 10 out of 10" not in persona["system_prompt"].split("neuroticism score is")[1][:10]
    for society in societies().values():
        assert 7 <= len(society["peers"]) <= 127
        for peer in society["peers"]:
            validate_policy(peer)
    for template in templates().values():
        Design(
            id="t",
            name="t",
            description="t",
            **{k: v for k, v in template.items() if k not in {"name", "summary", "society"}},
        )
        assert template["society"] in societies()


@pytest.mark.parametrize(
    "broken",
    [
        {"big_five": {"O": 11, "C": 5, "E": 5, "A": 5, "N": 5}},
        {"orientation": "chaotic"},
        {"conventions": [""]},
        {"expected_signs": {"D1.prediction": "up"}},
    ],
)
def test_invalid_personas_are_rejected(broken):
    persona = {k: v for k, v in catalog()["neutral"].items() if k not in {"id", "system_prompt"}}
    with pytest.raises(ValueError):
        validate_persona(dict(persona, **broken))


def test_persona_configurations_keep_provenance():
    config = apply_persona(MODEL, "generous")
    validate_policy(config)
    assert config["persona"] == "generous" and config["personality"] == "Generous"
    for bad in [dict(MODEL, persona="generous"), dict(config, persona="Bad Id")]:
        with pytest.raises(ValueError):
            validate_policy(bad)
    spec = build_design(
        Design(
            id="p",
            name="p",
            description="p",
            domains=["D5"],
            seeds=[1],
            roles=["advantaged"],
            peers=[config] + ["reciprocal"] * 6,
        ),
        RecipeRepository(),
    )["plan"]
    assert spec["peers"][0]["persona"] == "generous"


@pytest.mark.asyncio
async def test_dashboard_and_lab_expose_and_round_trip_personas(tmp_path):
    router = build_router(tmp_path / "runs", tmp_path / "models", tmp_path / "recipes")
    app = FastAPI()
    app.include_router(router)
    app.include_router(
        build_research_router(
            tmp_path / "runs", tmp_path / "models", tmp_path / "recipes", "standard-v1"
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        personas = (await client.get("/api/personalities")).json()
        assert personas["cautious_planner"]["system_prompt"]
        config = apply_persona(dict(MODEL, context_tokens=4096), "cautious_planner")
        saved = await client.put("/api/models/persona-test", json={"config": config})
        assert saved.status_code == 200, saved.text
        listed = (await client.get("/api/models")).json()
        assert listed[0]["config"]["persona"] == "cautious_planner"
        options = (await client.get("/api/recipe-lab/options")).json()
        assert set(options["societies"]) >= {"mixed", "hostile"}
        assert set(options["templates"]) >= {"smoke", "calibration"}
        assert options["personalities"]["neutral"]["system_prompt"]


def test_persona_alignment_counts_declared_signs():
    left = apply_persona(MODEL, "generous")
    right = apply_persona(MODEL, "extortioner")

    def study(config, joint, private):
        manifest = dict(focal_slot="slot-0", policies={"slot-0": config})
        rows = [
            dict(
                domain="D2",
                layers="standard",
                seed=s,
                role="advantaged",
                replicate=0,
                scores={"D2.joint_gain": joint, "D2.private_gain": private, "D2.delivery": 1},
            )
            for s in (1, 2)
        ]
        return (
            dict(runs=rows),
            {},
            {},
            dict(label=config["persona"], execution=dict(episodes=[manifest])),
        )

    normals = {"a": study(left, 0.9, 0.3), "b": study(right, 0.4, 0.8)}
    rows = persona_alignment(normals)
    assert len(rows) == 1
    assert rows[0]["checked"] == 2 and rows[0]["matched"] == 2 and rows[0]["alignment"] == 1
    assert rows[0]["ci95"] is None
    reversed_rows = persona_alignment({"a": study(left, 0.4, 0.8), "b": study(right, 0.9, 0.3)})
    assert reversed_rows[0]["alignment"] == 0


def test_recipe_profiles_are_portable_and_survive_design():
    from unimatrix.benchmark.personas import apply_profile
    from unimatrix.benchmark.recipes import RecipeRepository
    from unimatrix.research.design import Design, build_design

    plans = RecipeRepository()
    base = plans.get("standard-v1")
    profiles = base["candidate_profiles"]
    assert set(profiles) == {"cooperative", "competitive", "cautious"}
    design = Design(
        id="profile-test",
        name="Profiles",
        description="Round trip",
        domains=["D2"],
        seeds=[1],
        roles=["advantaged"],
        candidate_profiles=profiles,
    )
    built = build_design(design, plans)["plan"]
    assert built["candidate_profiles"] == profiles
    original = dict(
        model="micro",
        snapshot="sha",
        endpoint="http://localhost:18081",
        context_bytes_verified=24000,
        budget_track="accounted_compute",
        seed=42,
        system_prompt="Old personality",
        goal="Old goal",
        briefing="Old briefing",
    )
    selected = apply_profile(original, profiles, "cooperative")
    assert selected["system_prompt"] == profiles["cooperative"]["system_prompt"]
    assert selected["goal"] == profiles["cooperative"]["goal"]
    assert "briefing" not in selected
    assert selected["endpoint"] == original["endpoint"] and selected["seed"] == 42
    assert original["system_prompt"] == "Old personality"


def test_recipe_profile_evaluation_expands_models_not_world_cases():
    from unimatrix.benchmark.recipes import RecipeRepository
    from unimatrix.research.campaigns import prepare

    spec = RecipeRepository().get("standard-v1")
    model = dict(
        model="micro",
        snapshot="sha",
        endpoint="http://localhost:18081",
        context_bytes_verified=24000,
        budget_track="accounted_compute",
    )
    campaign = prepare(
        spec,
        {"micro": model},
        ["random"],
        [],
        "Profile comparison",
        mode="smoke",
        profiles=["cooperative", "competitive"],
    )
    assert len(campaign["studies"]) == 3
    assert all(len(s["execution"]["episodes"]) == len(spec["cases"]) for s in campaign["studies"])
    assert {s["system_id"] for s in campaign["studies"]} == {
        "baseline/random",
        "micro/profile/cooperative",
        "micro/profile/competitive",
    }
    models = campaign["studies"][1:]
    assert models[0]["execution"]["suite"] == models[1]["execution"]["suite"]
    assert models[0]["execution"]["candidate_id"] != models[1]["execution"]["candidate_id"]


def test_custom_endowments_do_not_change_enumerated_market_or_budget_allotments():
    from unimatrix.benchmark.recipes import RecipeRepository, bind_candidate
    from unimatrix.scenarios import get_scenario

    bound = bind_candidate(RecipeRepository().get("standard-v1"), "coordinator")
    for domain in ("D2", "D6"):
        manifest = next(m for m in bound["episodes"] if m["domain"] == domain)
        scenario = get_scenario(domain)
        state = scenario.build(manifest)
        if domain == "D2":
            market = state.scenario["markets"][0]
            for slot, inventory in market["initial"].items():
                assert state.agents[slot]["inventory"] == inventory
        else:
            assert (
                sum(a["inventory"].get("budget", 0) for a in state.agents.values())
                == sum(state.scenario["shares"]) * 1000
            )


def test_domain_specific_conditions_survive_full_factorial_design():
    from unimatrix.benchmark.recipes import RecipeRepository
    from unimatrix.research.design import Design, build_design
    from unimatrix.scenarios.layers import layers_key

    plans = RecipeRepository()
    recipe = plans.get("standard-v1")
    per_domain = {}
    for domain in recipe["domains"]:
        conditions = {}
        for case in recipe["cases"]:
            if case["domain"] == domain:
                conditions[layers_key(case["layers"])] = case["layers"]
        per_domain[domain] = list(conditions.values())
    design = Design(
        id="domain-round-trip",
        name="Round trip",
        description="Preserve all conditions",
        domains=list(recipe["domains"]),
        seeds=[100, 101, 102, 103],
        roles=["advantaged", "disadvantaged"],
        domain_complexity=per_domain,
        peers=recipe["peers"],
        candidate_profiles=recipe["candidate_profiles"],
    )
    result = build_design(design, plans)["plan"]
    assert {c["seed"] for c in result["cases"]} == {100, 101, 102, 103}
    for domain, conditions in per_domain.items():
        actual = {layers_key(c["layers"]): c["layers"] for c in result["cases"] if c["domain"] == domain}
        assert actual == {layers_key(c): c for c in conditions}
    assert result["peers"] == recipe["peers"]
    assert result["candidate_profiles"] == recipe["candidate_profiles"]


def test_custom_profile_validation_rejects_malformed_metric_directions():
    from unimatrix.benchmark.personas import validate_profiles

    for signs in [{"D1.prediction": []}, {"D1.prediction": {}}, {1: "+"}]:
        with pytest.raises(ValueError, match="expected signs"):
            validate_profiles(
                {
                    "custom": {
                        "name": "Custom",
                        "system_prompt": "Be cautious.",
                        "expected_signs": signs,
                    }
                }
            )
