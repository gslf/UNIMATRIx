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
from unimatrix.polis.report import build_report
from unimatrix.polis.runner import read_log, replay_seed, run_seed
from unimatrix.polis.service import SocietyService
from unimatrix.polis.simulate import calibrate, run, simulate
from unimatrix.polis.world import World


@pytest.fixture(scope="module")
def raw():
    return json.loads((BUNDLED / "standard-v2.1.json").read_text(encoding="utf8"))


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
    assert all(r["collapsed"] and 0 < r["total"] < r["earned"] for r in by["passive"])
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
    assert 0 < mean["expert"] < mean["oracle"]


def test_collapse_deducts_ten_percent_and_preserves_gross_domains(recipe):
    world = run(recipe, 1, "passive")
    final = world.ledger.final
    assert final["total"] == pytest.approx(final["earned"] * .9, abs=.001)
    assert final["penalty"] == round(final["earned"] * .1, 3)
    assert final["earned"] == pytest.approx(sum(d["points"] for d in final["domains"].values()))
    assert final["earned"] == pytest.approx(sum(world.frames[-1]["points"].values()), abs=0.05)
    assert final["collapsed"]["cause"] in {"revolt", "commons", "famine", "depopulation", "fragmentation",
                                           "legitimacy", "story"}
    assert world.done and len(world.frames) == final["collapsed"]["week"] + 1


@pytest.mark.parametrize("week", [1, 26, 52])
@pytest.mark.parametrize("gross", [0, 100, 2000, 20000, 1000000])
def test_penalty_is_scale_independent_idempotent_and_applies_on_last_week(week, gross):
    from unimatrix.polis.scoring import evaluate_score

    old = dict(total=0, earned=0, penalty=gross,
               domains={"D1": dict(points=gross)}, collapsed=dict(week=week))
    scored = evaluate_score(old)
    assert scored["total"] == gross * .9
    assert scored["penalty"] == gross * .1
    assert scored["earned"] == gross
    assert scored == evaluate_score(scored)
    assert evaluate_score(dict(old, collapsed=None))["total"] == gross


def test_scores_have_no_upper_bound(raw):
    boosted = copy.deepcopy(raw)
    boosted["scoring"]["rates"] = {d: v * 10 for d, v in boosted["scoring"]["rates"].items()}
    base = simulate(raw, 1, "expert", trace=False)["score"]["total"]
    assert simulate(boosted, 1, "expert", trace=False)["score"]["total"] == pytest.approx(base * 10, rel=1e-6)
    assert base > 100


def test_csv_separates_gross_points_and_final_collapse_penalty(recipe):
    import csv
    import io

    from unimatrix.web.polis import timeline_csv

    report = build_report(run(recipe, 1, "passive"))
    rows = list(csv.DictReader(io.StringIO(timeline_csv(report))))
    society = [row for row in rows if row["scope"] == "society"]
    assert all(row["final_score"] == "" and float(row["collapse_penalty"]) == 0
               for row in society[:-1])
    last, score = society[-1], report["score"]
    assert float(last["gross_points"]) == score["earned"]
    assert float(last["collapse_penalty"]) == score["penalty"] > 0
    assert float(last["final_score"]) == score["total"]
    assert last["scoring_rule"] == score["rule"]


def test_rescore_migrates_mixed_seeds_and_exports_without_replaying(tmp_path, recipe, monkeypatch, capsys):
    from unimatrix.main import cli
    from unimatrix.persistence.json_files import write_json
    from unimatrix.polis.scoring import SCORING_RULE

    service = SocietyService(tmp_path / "runs", PolisRecipes())
    ident = "a" * 24
    folder = service.folder(ident)
    recipe = dict(recipe, seeds=[1, 2])
    reports = {}
    entries = {}
    totals = []
    for seed, policy in [(1, "passive"), (2, "expert")]:
        world = run(recipe, seed, policy)
        report = build_report(world)
        report["frames"] = world.frames
        score = report["score"]
        totals.append(score["total"])
        score.pop("rule")
        if score["collapsed"]:
            score.update(total=0.0, penalty=score["earned"])
            report["diagnosis"][0]["text"] = "The score is zero; all points were forfeited."
        reports[str(seed)] = report
        entries[str(seed)] = dict(seed=seed, status="completed", week=world.frames[-1]["week"],
                                  total=score["total"], earned=score["earned"], collapsed=score["collapsed"])
        write_json(folder / f"seed-{seed}" / "report.json", report)
        (folder / f"seed-{seed}" / "decisions.jsonl").write_text('{"week":1,"raw":"original answer"}\n')
    record = dict(id=ident, model="m", model_id="m", snapshot="s", candidate_id="candidate",
                  recipe=recipe, recipe_id=recipe["id"], recipe_hash=digest(recipe), weeks=recipe["ticks"],
                  engine_fingerprint="original engine", status="completed", seeds=entries,
                  created_at="2026-10-12T10:00:00+00:00", updated_at="2026-10-12T11:00:00+00:00")
    record["score"] = service.aggregate(record)
    record["score"].pop("rule")
    write_json(folder / "run.json", record)
    write_json(folder / "report.json", service.combined(record))
    originals = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*.json")}
    logs = {p: p.read_bytes() for p in folder.rglob("*.jsonl")}
    # Migration must use the saved ledger, never replay an old model observation through a new engine.
    monkeypatch.setattr("unimatrix.polis.runner.replay_seed", lambda *a, **kw: pytest.fail("replay called"))
    assert cli(["rescore", "--runs-dir", str(service.directory), "--run", ident,
                "--explorer-dir", str(tmp_path / "explorer")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["rescored"] == [ident]
    corrected = service.read(ident)
    score = corrected["score"]
    assert score["rule"] == SCORING_RULE
    assert score["total"] == round(sum(totals) / 2, 3)
    assert score["totals"] == totals and score["collapsed"] == 1 and score["seeds"] == 2
    assert score["relative_to_expert"] == round(score["total"] / score["references"]["expert"], 3)
    assert score["references"]["passive"] > 0
    assert corrected["updated_at"] == record["updated_at"]
    assert corrected["engine_fingerprint"] == record["engine_fingerprint"]
    combined = service.report(ident)
    assert combined["score"] == combined["run"]["score"] == score
    assert service.leaderboard(recipe["id"])[0]["total"] == score["total"]
    for key, before in reports.items():
        after = combined["seeds"][key]
        assert after["score"]["total"] == totals[int(key) - 1]
        assert after["score"]["earned"] == before["score"]["earned"]
        assert "forfeited" not in after["score"]
        assert corrected["seeds"][key]["total"] == after["score"]["total"]
        assert after["frames"] == before["frames"] and after["score"]["domains"] == before["score"]["domains"]
    assert "collapse penalty (10%)" in combined["seeds"]["1"]["diagnosis"][0]["text"]
    assert score["penalty"] == round(combined["seeds"]["1"]["score"]["penalty"] / 2, 3)
    assert 'penalty: d"0.000"' in (tmp_path / "explorer" / "data" / "runs" / f"{ident}.xcdn").read_text(encoding="utf8")
    assert f'total: d"{score["total"]:.3f}"' in (tmp_path / "explorer" / "data" / "index.xcdn").read_text(encoding="utf8")
    assert not service.rescore(ident)
    for relative, content in originals.items():
        assert (folder / f"score-backup-before-{SCORING_RULE}" / relative).read_bytes() == content
    assert all(p.read_bytes() == content for p, content in logs.items())
    assert cli(["export", "--runs-dir", str(service.directory), "--run", ident,
                "--explorer-dir", str(tmp_path / "explorer")]) == 0
    assert json.loads(capsys.readouterr().out)["rescored"] == []
    corrected["status"] = "paused"
    write_json(folder / "run.json", corrected)
    with pytest.raises(ValueError, match="Only completed"):
        service.rescore(ident)


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


def test_every_inbox_message_is_shown_exactly_once(recipe, monkeypatch):
    sent = []
    original = World.message
    monkeypatch.setattr(World, "message", lambda self, *args, **kwargs: (
        original(self, *args, **kwargs), sent.append(self.inbox[-1])))
    world = World(recipe, 1)
    steward = make("expert")
    shown = []
    for _ in range(20):
        obs = world.observe()
        shown += [(m["sender"], m["text"]) for m in obs["inbox"]]
        world.step(steward.decide(obs))
    last = world.week - 1
    readable = [(m["sender"], m["text"]) for m in sent if m["kind"] == "story" or m["week"] < last]
    assert any(m["kind"] == "story" for m in sent)
    assert sorted(shown) == sorted(readable)


def test_system_prompt_is_static_and_lists_every_action(recipe):
    prompt = system_prompt(recipe)
    assert "10% of your accumulated points are deducted" in prompt
    assert "score becomes zero" not in prompt
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

    monkeypatch.setattr(llm, "StewardPolicy", lambda config, recipe, **shared: ScriptedPolicy())
    models = tmp_path / "models"
    models.mkdir()
    (models / "m.json").write_text(json.dumps({"model": "m", "snapshot": "s", "endpoint": "http://127.0.0.1:9",
                                                "budget_track": "opaque_compute", "context_bytes_verified": 24000}),
                                    encoding="utf8")
    router = build_polis_router(tmp_path / "runs", models, tmp_path / "societies",
                               tmp_path / "explorer")
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        recipes = (await client.get("/api/polis/recipes")).json()
        assert recipes[0]["id"] == "standard-v2.1" and recipes[0]["bundled"]
        raw = (await client.get("/api/polis/recipes/standard-v2.1")).json()["raw"]
        preview = (await client.post("/api/polis/simulate", json={"recipe": raw, "policy": "passive"})).json()
        assert preview["score"]["total"] > 0 and preview["score"]["collapsed"] and preview["frames"]
        saved = dict(raw, id="my-village", name="My village")
        assert (await client.put("/api/polis/recipes/my-village", json={"recipe": saved})).status_code == 200
        assert (await client.put("/api/polis/recipes/standard-v2.1", json={"recipe": raw})).status_code == 422
        bad = await client.post("/api/polis/recipes/validate", json={"recipe": dict(raw, ticks=3)})
        assert bad.json()["ok"] is False and "ticks" in bad.json()["error"]
        started = (await client.post("/api/polis/runs", json={"model_id": "m", "recipe_id": "standard-v2.1"})).json()
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
        assert snapshot["id"] == "standard-v2.1" and snapshot["hash"] == run["recipe_hash"]
        assert (await client.get("/api/polis/recipes/no-such-recipe")).status_code == 404
        csv_text = (await client.get(f"/api/polis/runs/{started['id']}/timeline.csv?seed=1")).text
        assert csv_text.startswith("scope,id,week,population") and "resident,r01," in csv_text
        decisions = (await client.get(f"/api/polis/runs/{started['id']}/decisions?seed=1&week=5")).json()
        assert decisions[0]["week"] == 5 and decisions[0]["observation"]["week"] == 5
        board = (await client.get("/api/polis/leaderboard?recipe=standard-v2.1")).json()
        assert board["rows"][0]["id"] == started["id"] and board["references"]["expert"]["total"] > 0
        exported = tmp_path / "explorer" / "data" / "runs" / f"{started['id']}.xcdn"
        assert exported.is_file() and exported.read_text(encoding="utf8").count("#week {") == 53
        assert exported.read_text(encoding="utf8").count("decision: #decision {") == 52
        assert started["id"] in (tmp_path / "explorer" / "data" / "index.xcdn").read_text(encoding="utf8")
        assert (await client.delete(f"/api/polis/runs/{started['id']}")).status_code == 200
        assert not exported.exists()


def test_authored_recipes_live_outside_the_bundle(tmp_path, raw):
    repository = PolisRecipes(tmp_path)
    repository.save(dict(raw, id="harsher", name="Harsher"))
    assert set(repository.all()) == {"standard-v2.1", "hell-v1", "harsher"}
    with pytest.raises(ValueError):
        repository.save(raw)
    repository.delete("harsher")
    assert list(repository.all()) == ["standard-v2.1", "hell-v1"]


MODEL = {"model": "m", "snapshot": "s", "endpoint": "http://127.0.0.1:9", "budget_track": "opaque_compute",
         "context_bytes_verified": 24000}


@pytest.mark.asyncio
async def test_parallel_seeds_share_one_model_instance(tmp_path, monkeypatch, raw):
    import unimatrix.polis.llm as llm

    shared = []

    def steward(config, recipe, **kw):
        shared.append(kw["instance"])
        return ScriptedPolicy()

    monkeypatch.setattr(llm, "StewardPolicy", steward)
    service = SocietyService(tmp_path, PolisRecipes(tmp_path / "s"))
    run = await service.start(MODEL, "m", normalize(dict(raw, id="twin", seeds=[1, 2])), parallelism=2)
    await service.active[run["id"]][0]
    assert service.read(run["id"])["status"] == "completed"
    assert len(shared) == 2 and shared[0] is shared[1] and shared[0].closed


@pytest.mark.asyncio
async def test_a_failing_seed_stops_its_parallel_siblings(tmp_path, monkeypatch, raw):
    import unimatrix.polis.llm as llm

    class Broken(ScriptedPolicy):
        async def decide(self, observation):
            if observation["week"] == 3:
                raise RuntimeError("provider exploded")
            return await super().decide(observation)

    policies = []
    monkeypatch.setattr(llm, "StewardPolicy", lambda config, recipe, **kw: policies.append(
        Broken() if not policies else ScriptedPolicy()) or policies[-1])
    service = SocietyService(tmp_path, PolisRecipes(tmp_path / "s"))
    run = await service.start(MODEL, "m", normalize(dict(raw, id="twin", seeds=[1, 2])), parallelism=2)
    await service.active[run["id"]][0]
    stored = service.read(run["id"])
    assert stored["status"] == "failed" and "provider exploded" in stored["error"]
    assert {s["status"] for s in stored["seeds"].values()} == {"paused"}


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


def test_command_line_lists_simulates_and_rejects_reference_exports(tmp_path, capsys):
    from unimatrix.main import cli

    assert cli(["--societies-dir", str(tmp_path / "s"), "recipes"]) == 0
    assert [r["id"] for r in json.loads(capsys.readouterr().out)] == ["standard-v2.1", "hell-v1"]
    assert cli(["simulate", "--policy", "passive", "--seed", "1"]) == 0
    score = json.loads(capsys.readouterr().out)["score"]
    assert score["collapsed"] and 0 < score["total"] < score["earned"]
    with pytest.raises(SystemExit):
        cli(["demo", "--explorer-dir", str(tmp_path / "explorer")])
    assert not (tmp_path / "explorer").exists()
    with pytest.raises(SystemExit):
        cli(["run", "--model", str(tmp_path / "missing.json")])
