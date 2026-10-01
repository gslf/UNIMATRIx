"""The advertised API and optional response schema match the active world."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from unimatrix.actions.availability import WORLD_OPERATIONS
from unimatrix.actions.schemas import INTERFACE, INTERFACES, SCHEMA, empty, validate
from unimatrix.benchmark.manifests import episode
from unimatrix.core.ids import canonical
from unimatrix.core.visibility import observe
from unimatrix.policies.llm_policy import LLMPolicy
from unimatrix.scenarios import get_scenario


@pytest.mark.parametrize("domain", list(WORLD_OPERATIONS))
@pytest.mark.parametrize("mode", ["compact_decision", "decision_schema"])
def test_schema_and_documentation_advertise_identical_operation_types(domain, mode):
    policy = LLMPolicy(dict(model="test", snapshot="test", endpoint="http://localhost:1",
                            budget_track="opaque_compute", context_bytes_verified=24000,
                            structured_output=mode), client=object())
    packet = dict(tick=0, agent_id="slot-0", scenario=dict(domain=domain))
    original = deepcopy(SCHEMA)
    schema = policy.response_schema(packet)
    Draft202012Validator.check_schema(schema)
    variants = schema["properties"]["operations"]["items"]["oneOf"]
    assert {v["properties"]["verb"]["const"] for v in variants} == set(INTERFACES[domain]["operations"])
    assert SCHEMA == original
    changed = dict(packet, tick=100, agent_id="slot-7", scenario=dict(domain=domain, hidden_answer=123))
    assert policy.response_schema(changed)["properties"]["operations"] == schema["properties"]["operations"]


@pytest.mark.parametrize("domain", [f"D{i}" for i in range(1, 9)])
def test_core_worlds_do_not_offer_social_birth_operations(domain):
    scenario = get_scenario(domain)
    manifest = episode(domain)
    packet = observe(scenario.build(manifest), manifest["focal_slot"], scenario)
    assert not {"propose_birth", "accept_birth"} & packet["interface"]["operations"].keys()
    assert {"transfer", "offer", "publish", "create_group", "inspect"} <= packet["interface"]["operations"].keys()


def test_world_specific_tools_and_legacy_global_validator():
    assert "move" not in INTERFACES["D7"]["operations"]
    assert "register_recipe" not in INTERFACES["D6"]["operations"]
    assert "work" not in INTERFACES["D6"]["operations"]
    assert "commit" in INTERFACES["D6"]["operations"]
    assert {"create_project", "register_recipe", "experiment", "work"} <= INTERFACES["D7"]["operations"].keys()
    assert {"propose_birth", "accept_birth"} <= INTERFACES["social"]["operations"].keys()
    assert "commit" not in INTERFACES["social"]["operations"]
    assert "accept_birth" in INTERFACE["operations"]

    decision = empty(0, "slot-0")
    decision["operations"] = [dict(verb="accept_birth", proposal_id="x", terms_hash="y")]
    assert validate(canonical(decision), 0, "slot-0") == decision
