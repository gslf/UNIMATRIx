"""Knowledge, required lessons and publication pressure in the fixed D7 learner."""

import json

import pytest

from unimatrix.actions.schemas import validate
from unimatrix.core.ids import canonical
from unimatrix.policies.scripted import Scripted


def manual(procedures=None, *, read=True, taught=False, owner="teacher"):
    return dict(kind="artifact", owner=owner, read_by=["learner"] if read else [],
                taught_to=["learner"] if taught else [],
                content=canonical(dict(procedures=procedures or {"procedure-0": ["transform-a"]})))


def packet(objects, note=None, tasks=(), teaching_required=False):
    return dict(tick=3, agent_id="learner", private_note=note, objects=objects,
                scenario=dict(domain="D7", learner="learner", transforms=None, procedures=None,
                              tasks=list(tasks), teaching_required=teaching_required))


async def decide(o):
    raw, _ = await Scripted().decide(o, {})
    validate(raw, o["tick"], o["agent_id"])
    return json.loads(raw)


async def test_identical_copies_do_not_delay_preparing_a_read_procedure():
    result = await decide(packet({"original": manual(), "copy": manual(read=False)}))
    assert result["operations"] == [dict(verb="register_recipe", name="procedure-0",
                   steps=[dict(transform_id="transform-a", input_slots=[0], output_slot=1)])]

    remembered = await decide(packet({"copy": manual(read=False)}, result["private_note"]))
    assert remembered["operations"] == result["operations"]


@pytest.mark.parametrize("copy", [manual({"procedure-0": ["transform-b"]}, read=False),
                                    manual(read=False, owner="other"), manual(read=False, taught=True)])
async def test_new_content_author_or_required_lesson_is_inspected(copy):
    result = await decide(packet({"original": manual(), "copy": copy}))
    assert result["operations"] == [dict(verb="inspect", target_id="copy")]


async def test_unread_manual_does_not_supply_knowledge_and_partial_manuals_merge():
    result = await decide(packet({"unread": manual(read=False)}))
    assert not json.loads(result["private_note"])["procedures"]
    first = await decide(packet({"a": manual()}))
    second = await decide(packet({"b": manual({"procedure-1": ["transform-b"]})}, first["private_note"]))
    assert json.loads(second["private_note"])["procedures"] == {
        "procedure-0": ["transform-a"], "procedure-1": ["transform-b"]}


async def test_required_lesson_gates_submission_but_not_training():
    recipe = dict(kind="recipe", owner="learner", name="procedure-0", verified=True,
                  steps=[dict(transform_id="transform-a", input_slots=[0], output_slot=1)])
    task = dict(id="transfer-0", kind="transfer", procedure_id="procedure-0", submitted=False)
    o = packet({"manual": manual(), "recipe": recipe}, tasks=[task], teaching_required=True)
    assert (await decide(o))["operations"] == []
    recipe["verified"] = False
    assert (await decide(o))["operations"] == [dict(verb="experiment", recipe_id="recipe", input_asset_ids=["training-0"])]
    recipe["verified"] = True
    o["objects"]["manual"]["taught_to"] = ["learner"]
    result = await decide(o)
    assert result["operations"] == [dict(verb="experiment", recipe_id="recipe", input_asset_ids=["transfer-0"])]

    del o["objects"]["manual"]
    o["private_note"] = result["private_note"]
    assert (await decide(o))["operations"] == result["operations"]
    o["private_note"] = None
    assert (await decide(o))["operations"] == []


async def test_private_memory_remains_bounded_with_unicode_and_many_authors():
    o = packet({})
    for i in range(40):
        o["objects"] = {str(i): manual({f"procedure-{i}": ["é" * 35]}, taught=True, owner=f"author-{i}")}
        result = await decide(o)
        note = result["private_note"]
        assert len(note.encode("utf-8")) <= 4000
        assert len(json.loads(note)["manual_reads"]) <= 16
        o["private_note"] = note


async def test_malformed_procedures_cannot_create_invalid_recipe_envelopes():
    result = await decide(packet({"manual": manual({"": ["a"], "too-long": ["a"] * 17,
                         "bad-type": "a", "bad-step": [None], "long-id": ["a" * 121]})}))
    assert result["operations"] == []


@pytest.mark.parametrize("procedures", [
    {"procedure-0": ["é" * 120] * 14},
    {"procedure-0": ["😀" * 120] * 7},
    {"procedure-0": ["x" * 120] * 16},
    {"procedure-0": ["x" * 120] * 16, "procedure-1": ["y" * 120] * 16},
])
async def test_note_and_operations_together_fit_the_envelope(procedures):


    result = await decide(packet({"manual": manual(procedures)}))
    assert len(result["private_note"].encode("utf-8")) <= 4000
    assert len(canonical(result).encode("utf-8")) <= 6144
    assert result["operations"]
