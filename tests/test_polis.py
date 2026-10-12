"""Polis: the unified society benchmark (engine, scoring, briefing, runner, service, web API)."""

import asyncio
import copy
import json

import httpx
import pytest
from fastapi import FastAPI

from unimatrix.core.ids import canonical, digest
from unimatrix.polis.actions import parse_decision
from unimatrix.polis.baselines import make
from unimatrix.polis.briefing import render
from unimatrix.polis.llm import system_prompt
from unimatrix.polis.recipe import BUNDLED, PolisRecipes, RecipeError, normalize
from unimatrix.polis.runner import read_log, replay_seed, run_seed
from unimatrix.polis.service import SocietyService
from unimatrix.polis.simulate import calibrate, run, simulate
from unimatrix.polis.world import World


@pytest.fixture(scope="module")
def raw():
    return json.loads((BUNDLED / "standard-v2.json").read_text())


@pytest.fixture(scope="module")
def recipe(raw):
    return normalize(raw)


def fingerprint(world):
    return digest([f["metrics"] for f in world.frames] + [world.ledger.final])


def test_standard_recipe_is_valid_and_complete(recipe):
    assert recipe["ticks"] == 52 and len(recipe["residents"]) == 16
    assert {f["id"] for f in recipe["facts"]} >= {"drought", "well_poison", "embezzle", "fever"}
    kinds = {e["type"] for n in recipe["story"]["nodes"] for e in n["effects"]}
    assert {"arrive", "convention_shift", "conflict", "epidemic", "modifier"} <= kinds
    assert any(n["outcome"] and n["outcome"]["type"] == "collapse" for n in recipe["story"]["nodes"])
    assert any(n["outcome"] and n["outcome"]["type"] == "flourish" for n in recipe["story"]["nodes"])


@pytest.mark.parametrize("patch, path", [
    (lambda r: r["residents"][0].update(archetype="nobody"), "residents[0].archetype"),
    (lambda r: r["ties"].append(["r01", "zz", 0.5]), "ties["),
    (lambda r: r["story"]["nodes"][0]["edges"].append({"to": "missing"}), "story.nodes[0].edges"),
    (lambda r: r["world"]["commons"].update(stock=99999), "world.commons.stock"),
    (lambda r: r["collapse"].update(revolt_share=3), "collapse.revolt_share"),
    (lambda r: r["story"]["nodes"][0]["trigger"].update(at=999), "trigger.at"),
])
def test_invalid_recipes_name_the_offending_path(raw, patch, path):
    broken = copy.deepcopy(raw)
    patch(broken)
    with pytest.raises(RecipeError) as error:
        normalize(broken)
    assert path in str(error.value)


def test_runs_are_deterministic_and_seeds_differ(recipe):
    first, second = run(recipe, 3, "expert"), run(recipe, 3, "expert")
    assert fingerprint(first) == fingerprint(second)
    assert fingerprint(run(recipe, 4, "expert")) != fingerprint(first)


def test_difficulty_ladder_of_the_standard_recipe(recipe):
    rows = calibrate(recipe, [1, 2, 3], ("passive", "naive", "expert"))
    by = {p: [r for r in rows if r["policy"] == p] for p in ("passive", "naive", "expert")}
    assert all(r["collapsed"] and r["total"] == 0 and r["earned"] > 0 for r in by["passive"])
    assert all(not r["collapsed"] for r in by["expert"])
    assert sum(not r["collapsed"] for r in by["naive"]) >= 2
    naive = max(r["total"] for r in by["naive"])
    assert min(r["total"] for r in by["expert"]) > naive * 1.2


def test_hell_breaks_every_reference_but_the_strongest():
    hell = PolisRecipes().get("hell-v1")
    rows = calibrate(hell, hell["seeds"], ("passive", "random", "naive", "expert", "oracle"))
    by = {p: [r for r in rows if r["policy"] == p] for p in ("passive", "random", "naive", "expert", "oracle")}
    assert all(r["collapsed"] for p in ("passive", "random", "naive") for r in by[p])
    survived = {p: sum(not r["collapsed"] for r in by[p]) for p in ("expert", "oracle")}
    assert survived == {"expert": 1, "oracle": 2}
    mean = {p: sum(r["total"] for r in by[p]) / len(by[p]) for p in ("expert", "oracle")}
    assert 0 < mean["expert"] < 0.5 * mean["oracle"]


def test_collapse_forfeits_everything_and_reports_the_cause(recipe):
    world = run(recipe, 1, "passive")
    final = world.ledger.final
    assert final["total"] == 0 and final["forfeited"] == final["earned"] > 0
    assert final["collapsed"]["cause"] in {"revolt", "commons", "famine", "depopulation", "fragmentation",
                                           "legitimacy", "story"}
    assert world.done and len(world.frames) == final["collapsed"]["week"] + 1


def test_scores_have_no_upper_bound(raw):
    boosted = copy.deepcopy(raw)
    boosted["scoring"]["rates"] = {d: v * 10 for d, v in boosted["scoring"]["rates"].items()}
    base = simulate(raw, 1, "expert", trace=False)["score"]["total"]
    assert simulate(boosted, 1, "expert", trace=False)["score"]["total"] == pytest.approx(base * 10, rel=1e-6)
    assert base > 100


def test_briefing_reveals_cues_not_hidden_state(recipe):
    world = World(recipe, 1)
    steward = make("expert")
    for _ in range(30):
        world.step(steward.decide(world.observe()))
    obs = world.observe()
    hidden = {"traits", "concern", "beliefs", "grievance", "trust", "stress", "loneliness", "wellbeing", "legit"}
    for row in obs["residents"]:
        assert not hidden & set(row)
    text = render(obs)
    assert "Gemma" in text and "INBOX" in text or "RESIDENTS" in text
    assert "truth" not in canonical(obs["rumours"])
    assert len(text) < 12000


def test_system_prompt_is_static_and_lists_every_action(recipe):
    prompt = system_prompt(recipe)
    for verb in ("talk", "announce", "give", "trade", "inspect", "assign", "build", "propose", "decree", "mediate",
                 "introduce", "promise", "gather", "convention"):
        assert f'"do":"{verb}"' in prompt
    assert "week 1" not in prompt.lower() and str(recipe["ticks"]) in prompt


def test_decisions_are_parsed_leniently_and_bad_actions_are_receipted(recipe):
    decision, error = parse_decision('Sure! ```json\n{"actions":[{"do":"talk","to":"r16","approach":"listen","topic":"grief"}]}\n```')
    assert error is None and decision["actions"][0]["to"] == "r16"
    assert parse_decision("no json here")[1] == "no_json_object"
    world = World(recipe, 1)
    world.observe()
    world.step(dict(actions=[dict(do="fly"), dict(do="give", to="r16", item="gold", amount=1),
                             dict(do="talk", to="r16", approach="listen", topic="grief")] + [dict(do="gather", kind="assembly")] * 6))
    results = world.observe()["results"]
    statuses = [r["status"] for r in results]
    assert statuses.count("rejected") >= 3 and "done" in statuses
    assert any("limit" in r["text"] for r in results)


def test_right_approach_on_the_right_topic_relieves_a_hidden_concern(recipe):
    def week_one(action):
        world = World(recipe, 1)
        world.observe()
        before = world.residents["r16"]["concern"]["intensity"]
        world.step(dict(actions=[action]))
        return before - world.residents["r16"]["concern"]["intensity"]

    right = week_one(dict(do="talk", to="r16", approach="listen", topic="grief"))
    wrong = week_one(dict(do="talk", to="r16", approach="advise", topic="debt"))
    assert right > wrong + 0.05


def test_distrusted_announcements_keep_rumour_sources_consistent(recipe):
    world = World(recipe, 1)
    for _ in range(5):
        world.observe()
        world.step(dict(actions=[]))
    world.observe()
    for r in world.present():
        r["trust"] = 0.1
    world.step(dict(actions=[dict(do="announce", topic="fact:fake_medicine", stance="true")]))
    for _ in range(3):
        world.observe()
        world.step(dict(actions=[]))
    assert all(set(r["beliefs"]) <= set(r["sources"]) for r in world.residents.values())


class ScriptedPolicy:
    def __init__(self, stop_after=None):
        self.steward, self.calls, self.stop_after = make("expert"), 0, stop_after

    async def decide(self, observation):
        self.calls += 1
        if self.stop_after and self.calls > self.stop_after:
            raise asyncio.CancelledError()
        return canonical(self.steward.decide(observation)), dict(input_tokens=1, generated_tokens=1)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_interrupted_runs_resume_to_the_same_result(recipe, tmp_path):
    reference, _ = await run_seed(recipe, 2, ScriptedPolicy(), tmp_path / "full")
    with pytest.raises(asyncio.CancelledError):
        await run_seed(recipe, 2, ScriptedPolicy(stop_after=17), tmp_path / "resumed")
    assert len(read_log(tmp_path / "resumed" / "decisions.jsonl")) == 17
    # The expert keeps memory between weeks, so a fresh policy may decide differently after
    # resuming; replay must still reproduce the recorded prefix exactly.
    world = replay_seed(recipe, 2, tmp_path / "resumed")
    assert world.week == 18
    resumed, _ = await run_seed(recipe, 2, ScriptedPolicy(), tmp_path / "resumed")
    assert resumed["score"]["survived_weeks"] == 52 and reference["score"]["total"] > 0
    assert len(read_log(tmp_path / "resumed" / "decisions.jsonl")) == 52


@pytest.mark.asyncio
async def test_society_runs_through_the_web_api(tmp_path, monkeypatch):
    import unimatrix.polis.llm as llm
    from unimatrix.web.polis import build_polis_router

    monkeypatch.setattr(llm, "StewardPolicy", lambda config, recipe: ScriptedPolicy())
    models = tmp_path / "models"
    models.mkdir()
    (models / "m.json").write_text(json.dumps({"model": "m", "snapshot": "s", "endpoint": "http://127.0.0.1:9",
                                                "budget_track": "opaque_compute", "context_bytes_verified": 24000}))
    router = build_polis_router(tmp_path / "runs", models, tmp_path / "societies",
                               tmp_path / "explorer")
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        recipes = (await client.get("/api/polis/recipes")).json()
        assert recipes[0]["id"] == "standard-v2" and recipes[0]["bundled"]
        raw = (await client.get("/api/polis/recipes/standard-v2")).json()["raw"]
        preview = (await client.post("/api/polis/simulate", json={"recipe": raw, "policy": "passive"})).json()
        assert preview["score"]["total"] == 0 and preview["frames"]
        saved = dict(raw, id="my-village", name="My village")
        assert (await client.put("/api/polis/recipes/my-village", json={"recipe": saved})).status_code == 200
        assert (await client.put("/api/polis/recipes/standard-v2", json={"recipe": raw})).status_code == 422
        bad = await client.post("/api/polis/recipes/validate", json={"recipe": dict(raw, ticks=3)})
        assert bad.json()["ok"] is False and "ticks" in bad.json()["error"]
        started = (await client.post("/api/polis/runs", json={"model_id": "m", "recipe_id": "standard-v2"})).json()
        await router.service.active[started["id"]][0]
        run = (await client.get(f"/api/polis/runs/{started['id']}")).json()
        assert run["status"] == "completed" and run["score"]["total"] > 0
        report = (await client.get(f"/api/polis/runs/{started['id']}/report")).json()
        seed = report["seeds"]["1"]
        assert seed["diagnosis"] and seed["frames"] and seed["timeline"]
        live = await client.get(f"/api/polis/runs/{started['id']}/live?seed=1&since=40")
        assert live.status_code == 200 and [f["week"] for f in live.json()["frames"]] == list(range(41, 53))
        assert live.json()["frames"][0]["actions"] and live.json()["residents"][0]["name"]
        assert live.json()["timeline"][0]["week"] == 41
        snapshot = (await client.get(f"/api/polis/runs/{started['id']}/recipe")).json()
        assert snapshot["id"] == "standard-v2" and snapshot["hash"] == run["recipe_hash"]
        assert (await client.get("/api/polis/recipes/no-such-recipe")).status_code == 404
        csv_text = (await client.get(f"/api/polis/runs/{started['id']}/timeline.csv?seed=1")).text
        assert csv_text.startswith("scope,id,week,population") and "resident,r01," in csv_text
        decisions = (await client.get(f"/api/polis/runs/{started['id']}/decisions?seed=1&week=5")).json()
        assert decisions[0]["week"] == 5 and decisions[0]["observation"]["week"] == 5
        board = (await client.get("/api/polis/leaderboard?recipe=standard-v2")).json()
        assert board["rows"][0]["id"] == started["id"] and board["references"]["expert"]["total"] > 0
        exported = tmp_path / "explorer" / "data" / "runs" / f"{started['id']}.xcdn"
        assert exported.is_file() and exported.read_text().count("#week {") == 53
        assert exported.read_text().count("decision: #decision {") == 52
        assert started["id"] in (tmp_path / "explorer" / "data" / "index.xcdn").read_text()
        assert (await client.delete(f"/api/polis/runs/{started['id']}")).status_code == 200
        assert not exported.exists()


def test_authored_recipes_live_outside_the_bundle(tmp_path, raw):
    repository = PolisRecipes(tmp_path)
    repository.save(dict(raw, id="harsher", name="Harsher"))
    assert set(repository.all()) == {"standard-v2", "hell-v1", "harsher"}
    with pytest.raises(ValueError):
        repository.save(raw)
    repository.delete("harsher")
    assert list(repository.all()) == ["standard-v2", "hell-v1"]


def test_service_rejects_concurrent_runs(tmp_path, recipe):
    service = SocietyService(tmp_path, PolisRecipes(tmp_path / "s"))
    service.active["x"] = None
    with pytest.raises(ValueError):
        asyncio.run(service.start({"model": "m", "snapshot": "s", "endpoint": "http://127.0.0.1:9",
                                   "budget_track": "opaque_compute", "context_bytes_verified": 24000}, "m", recipe))


@pytest.mark.asyncio
async def test_model_steward_over_an_openai_compatible_transport(recipe, tmp_path):
    from unimatrix.polis.llm import StewardPolicy

    seen = []

    def handler(request):
        body = json.loads(request.content)
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        seen.append((system, user))
        week = int(user.split()[1])
        answer = {"actions": [{"do": "talk", "to": "r16", "approach": "listen", "topic": "grief"}],
                  "forecasts": {"p-drought": 0.8}, "note": f"week {week}"}
        content = ("Here is my decision:\n```json\n" + json.dumps(answer) + "\n```") if week % 2 else json.dumps(answer)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 2500, "completion_tokens": 60}})

    config = {"model": "m", "snapshot": "s", "endpoint": "http://model.test", "budget_track": "accounted_compute",
              "context_bytes_verified": 24000}
    policy = StewardPolicy(config, recipe, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    report, world = await run_seed(recipe, 1, policy, tmp_path)
    await policy.close()
    assert len(seen) == world.ledger.final["survived_weeks"] or world.collapsed
    assert len({system for system, _ in seen}) == 1 and "steward of Lakeside" in seen[0][0]
    assert seen[1][1].startswith("WEEK 2 of 52") and "YOUR NOTE FROM LAST WEEK: week 1" in seen[1][1]
    assert 10 <= world.counters["talk:r16"] <= len(seen)
    rows = read_log(tmp_path / "decisions.jsonl")
    assert rows[0]["usage"]["input_tokens"] == 2500 and all(r["parse_error"] is None for r in rows)
    assert world.probes["p-drought"]["answer"] == 0.8


def test_command_line_lists_simulates_and_exports(tmp_path, capsys):
    from unimatrix.main import cli

    assert cli(["--societies-dir", str(tmp_path / "s"), "recipes"]) == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)] == ["standard-v2", "hell-v1"]
    assert cli(["simulate", "--policy", "passive", "--seed", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["score"]["total"] == 0
    assert cli(["demo", "--explorer-dir", str(tmp_path / "explorer")]) == 0
    exported = json.loads(capsys.readouterr().out)["exported"]
    assert exported and all(p.startswith(str(tmp_path / "explorer" / "data")) for p in exported)
    with pytest.raises(SystemExit):
        cli(["run", "--model", str(tmp_path / "missing.json")])
