import asyncio
import json
import sqlite3
from copy import deepcopy

import httpx
import pytest

from tests.test_benchmark_core import setup
from tests.test_benchmark_web import MODEL
from tests.test_benchmark_web import setup as web_setup
from unimatrix.actions.schemas import empty
from unimatrix.benchmark.runner import InfrastructureFailure, Runner
from unimatrix.benchmark.validation import validate_policy
from unimatrix.core.ids import canonical
from unimatrix.persistence.event_store import EventStore
from unimatrix.persistence.replay import backup, replay
from unimatrix.policies.llm_policy import LLMPolicy
from unimatrix.policies.router import Router
from unimatrix.policies.scripted import Scripted
from unimatrix.web.server import build_app


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed", "3"),
        ("seed", True),
        ("temperature", "0"),
        ("temperature", float("nan")),
        ("max_output_tokens", 0),
        ("request_timeout_seconds", -1),
        ("max_input_tokens", False),
        ("output_price_per_million", float("inf")),
    ],
)
def test_strict_model_numbers(field, value):
    with pytest.raises(ValueError):
        validate_policy(dict(MODEL, **{field: value}))


@pytest.mark.asyncio
async def test_total_deadline_and_cancellation():
    async def blocked(request):
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
        policy = LLMPolicy(dict(MODEL, request_timeout_seconds=0.01), client=client)
        with pytest.raises(InfrastructureFailure, match="provider_decision_timeout"):
            await asyncio.wait_for(policy.decide({}, {}), timeout=1)
        policy.request_timeout_seconds = 120
        task = asyncio.create_task(policy.decide({}, {}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_explicit_resume_recovers_exhausted_window_without_repeating_successes(tmp_path):
    manifest, scenario, state, store = setup(tmp_path)

    class Recovering:
        fingerprint = "recovering"
        count = 0

        async def decide(self, packet, budget):
            self.count += 1
            if self.count <= 3:
                raise InfrastructureFailure("temporary")
            return canonical(empty(packet["tick"], packet["agent_id"])), {}

    policy = Recovering()
    bindings = {s: Scripted("passive") for s in state.agents}
    bindings[manifest["focal_slot"]] = policy
    runner = Runner(store, scenario, Router(bindings))
    try:
        with pytest.raises(InfrastructureFailure):
            await runner.run(1)
        successful = store.db.execute("SELECT slot,raw FROM decisions ORDER BY slot").fetchall()
        await runner.run(1)
        assert policy.count == 4
        assert store.status()["completed_tick"] == 1
        assert all(
            store.db.execute("SELECT raw FROM decisions WHERE tick=0 AND slot=?", (s,)).fetchone()[
                0
            ]
            == raw
            for s, raw in successful
        )
        calls = store.db.execute(
            "SELECT attempt,body FROM model_calls WHERE request_id IN (SELECT request_id FROM decisions WHERE slot=?) ORDER BY attempt",
            (manifest["focal_slot"],),
        ).fetchall()
        assert [a for a, _ in calls] == [0, 1, 2, 3]
        assert [json.loads(b)["retry_window"] for _, b in calls] == [0, 0, 0, 1]
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tick", [0, 1, 2])
async def test_snapshot_tamper_detected_after_cached_verification(tmp_path, tick):
    manifest, scenario, _, store = setup(tmp_path)
    try:
        await Runner(store, scenario, Router.scripted(manifest)).run(2)
        store.verify()
        data = store.snapshot(tick)
        data["scenario"]["audit_tamper"] = True
        with sqlite3.connect(store.path) as outside:
            outside.execute(
                "UPDATE snapshots SET state=? WHERE tick=?", (store._pack(canonical(data)), tick)
            )
        with pytest.raises(ValueError, match="snapshot_hash_mismatch"):
            store.verify()
        with pytest.raises(ValueError, match="snapshot_hash_mismatch"):
            store.snapshot(tick)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_true_replay_and_wal_backup(tmp_path):
    manifest, scenario, _, store = setup(tmp_path)
    try:
        await Runner(store, scenario, Router.scripted(manifest)).run(4)
        assert replay(store)["transitions_recomputed"] == 4
        target = tmp_path / "backup.db"
        assert backup(store, target) == store.verify()
        with pytest.raises(FileExistsError):
            backup(store, target)
        restored = EventStore(target, read_only=True)
        try:
            assert replay(restored)["providers_called"] == 0
        finally:
            restored.close()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_frozen_recipe_survives_live_catalog_change(tmp_path, monkeypatch):
    _, router, spec = web_setup(tmp_path)
    original = deepcopy(spec)

    async def decide(self, packet, budget):
        return canonical(empty(packet["tick"], packet["agent_id"])), {"generated_tokens": 1}

    monkeypatch.setattr(LLMPolicy, "decide", decide)
    import unimatrix.benchmark.service as module

    actual = module.ensure_references

    async def checked(spec, *args):
        assert spec == original
        return await actual(spec, *args)

    monkeypatch.setattr(module, "ensure_references", checked)
    try:
        run = await router.service.start(MODEL, "a")
        (tmp_path / "plans" / "test.json").unlink()
        await router.service.active[run["id"]][0]
        result = router.service.read(run["id"])
        assert result["status"] == "completed", result.get("error")
        assert not result.get("reference_error")
        assert result["report"]["rating"] is not None
    finally:
        await router.shutdown_tasks()


@pytest.mark.asyncio
async def test_dashboard_security_and_credential_confinement(tmp_path):
    app = build_app(tmp_path / "runs", tmp_path / "models", tmp_path / "recipes")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        assert (await client.get("/", headers={"Host": "attacker.example"})).status_code == 400
        assert (
            await client.get("/api/models", headers={"Origin": "https://attacker.example"})
        ).status_code == 403
        assert (await client.put("/api/models/a", json={"config": MODEL})).status_code == 403
        headers = {"X-Unimatrix-Request": "1"}
        assert (
            await client.put(
                "/api/models/a",
                headers=headers,
                json={"config": dict(MODEL, api_key_file="/tmp/secret")},
            )
        ).status_code == 422
        saved = await client.put(
            "/api/models/a", headers=headers, json={"config": MODEL, "api_key": "local-key"}
        )
        assert saved.status_code == 200
        changed = dict(saved.json(), endpoint="https://attacker.example")
        assert (
            await client.put("/api/models/a", headers=headers, json={"config": changed})
        ).status_code == 422
        assert (
            await client.put(
                "/api/models/a", headers=dict(headers, Origin="null"), json={"config": MODEL}
            )
        ).status_code == 403
        assert (await client.get("/")).headers["x-frame-options"] == "DENY"
    token = "t" * 32
    app = build_app(
        tmp_path / "runs",
        tmp_path / "models",
        tmp_path / "recipes",
        auth_token=token,
        allowed_hosts=["research.internal"],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://research.internal"
    ) as client:
        assert (await client.get("/api/models")).status_code == 401
        assert (
            await client.get("/api/models", headers={"Authorization": "Bearer " + token})
        ).status_code == 200
        assert (await client.get("/", auth=("operator", token))).status_code == 200


@pytest.mark.parametrize("domain", [f"D{i}" for i in range(1, 9)])
async def test_every_domain_replays_identically_across_restart(tmp_path, domain):
    from unimatrix.benchmark.recipes import RecipeRepository, bind_candidate
    from unimatrix.scenarios import get_scenario

    manifest = next(
        m
        for m in bind_candidate(RecipeRepository().get("standard-v1"), "coordinator")["episodes"]
        if m["domain"] == domain
    )
    scenario = get_scenario(domain)

    def open_store(path):
        store = EventStore(path)
        store.initialize(manifest, scenario.build(manifest))
        return store

    uninterrupted = open_store(tmp_path / "uninterrupted.db")
    resumed = open_store(tmp_path / "resumed.db")
    try:
        await Runner(uninterrupted, scenario, Router.scripted(manifest)).run(23)
        await Runner(resumed, scenario, Router.scripted(manifest)).run(7)
        resumed.close()
        resumed = open_store(tmp_path / "resumed.db")
        await Runner(resumed, scenario, Router.scripted(manifest)).run(23)
        assert resumed.verify() == uninterrupted.verify()
        assert replay(resumed)["transitions_recomputed"] == 23
    finally:
        uninterrupted.close()
        resumed.close()
