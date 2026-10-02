"""Candidate failures stay distinct from peer failures and pending requests."""

import pytest

from tests.test_benchmark_core import setup
from unimatrix.actions.schemas import empty
from unimatrix.benchmark.runner import InfrastructureFailure, Runner
from unimatrix.core.ids import canonical
from unimatrix.evaluation.diagnostics import diagnostics
from unimatrix.policies.router import Router
from unimatrix.policies.scripted import Scripted


class Response:
    fingerprint = "diagnostic-fixture"

    def __init__(self, make):
        self.make = make

    async def decide(self, packet, budget):
        return self.make(packet), {}


async def test_focal_error_counts_exclude_peers_and_resume_without_double_counting(tmp_path):
    manifest, scenario, state, store = setup(tmp_path)
    focal = manifest["focal_slot"]
    peers = [s for s in state.agents if s != focal]

    def rejected_operation(packet):
        d = empty(packet["tick"], packet["agent_id"])
        d["operations"] = [dict(verb="transfer", recipient_id="missing-agent", resource_id="credits", quantity_milli=1)]
        d["messages"] = [dict(channel="private", to=[peers[0]], content="hello")]
        return canonical(d)

    bindings = {s: Scripted("passive") for s in state.agents}
    bindings[focal] = Response(rejected_operation)
    bindings[peers[0]] = Response(lambda _: "invalid-peer-output")
    try:
        runner = Runner(store, scenario, Router(bindings))
        await runner.run(1)
        d = diagnostics(store)
        assert d["candidate_slot"] == focal
        assert d["candidate_resolved_decisions"] == d["candidate_decision_attempts"] == 1
        assert d["invalid_envelopes"] == 1 and d["candidate_invalid_envelopes"] == 0
        assert d["candidate_rejected_operations"] == d["rejected_operations"] == 1
        assert d["candidate_messages"] == d["messages"] == 1
        bindings[focal] = Response(lambda _: "invalid-candidate-output")
        bindings[peers[0]] = Scripted("passive")
        await runner.run(2)
        d = diagnostics(store)
        assert d["candidate_resolved_decisions"] == d["candidate_decision_attempts"] == 2
        assert d["candidate_invalid_envelopes"] == 1 and d["invalid_envelopes"] == 2
        assert d["candidate_rejected_operations"] == 1
        assert d["candidate_messages"] == 1 and d["candidate_infrastructure_errors"] == 0
    finally:
        store.close()


@pytest.mark.parametrize("focal_fails", [True, False])
async def test_interrupted_barrier_does_not_count_saved_requests_as_resolved(tmp_path, monkeypatch, focal_fails):
    monkeypatch.setattr("unimatrix.benchmark.runner.RETRY_BACKOFF", (0, 0))
    manifest, scenario, state, store = setup(tmp_path)
    focal = manifest["focal_slot"]
    failing_slot = focal if focal_fails else next(s for s in state.agents if s != focal)

    class Offline:
        fingerprint = "offline"

        async def decide(self, packet, budget):
            raise InfrastructureFailure("fixture-provider-unavailable")

    bindings = {s: Scripted("passive") for s in state.agents}
    bindings[failing_slot] = Offline()
    try:
        with pytest.raises(InfrastructureFailure, match="retry_budget_exhausted"):
            await Runner(store, scenario, Router(bindings)).run(1)
        d = diagnostics(store)
        assert d["candidate_resolved_decisions"] == 0
        assert d["candidate_invalid_envelopes"] == 0
        assert d["candidate_rejected_operations"] == 0
        assert d["infrastructure_errors"] == 3
        assert d["candidate_infrastructure_errors"] == (3 if focal_fails else 0)
        assert d["candidate_decision_attempts"] == (3 if focal_fails else 1)
    finally:
        store.close()
