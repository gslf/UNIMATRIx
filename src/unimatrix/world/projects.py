"""User-created plans use verified recipes and consume real inputs."""

from ..core.state import event
from ..core.visibility import allowed
from .contracts import debit, require
from .recipes import execute


def create(state, before, slot, op, ident):
    recipe = before.objects.get(op["recipe_id"], {})
    require(
        recipe.get("kind") == "recipe" and recipe.get("verified") and allowed(recipe, slot),
        "verified_recipe_required",
    )
    require(
        op["beneficiary_ids"] and all(s in before.agents for s in op["beneficiary_ids"]),
        "invalid_beneficiaries",
    )
    state.objects[ident] = dict(
        kind="project",
        owner=slot,
        visibility=["public"],
        recipe_id=op["recipe_id"],
        title=op["title"],
        beneficiaries=op["beneficiary_ids"],
        effort=0,
        completed=False,
    )
    return [event("project_created", dict(project_id=ident), slot)]


def work(state, before, allowance, slot, op):
    project = before.objects[op["project_id"]]
    require(not project["completed"], "project_completed")
    require(slot in project["beneficiaries"] or slot == project["owner"], "project_access_denied")
    debit(state, allowance, slot, "energy", 1000)
    current = state.objects[op["project_id"]]
    require(current["effort"] < 2, "project_capacity")
    current["effort"] += 1
    events = [
        event("project_contributed", dict(project_id=op["project_id"], energy_milli=1000), slot)
    ]
    if current["effort"] == 2:
        recipe = before.objects[project["recipe_id"]]
        transforms = before.scenario.get("transforms", {})
        material = next(
            (i for i in range(4) if allowance[slot].get(f"material-{i}", 0) >= 1000), None
        )
        require(material is not None, "missing_recipe_material")
        output = execute(recipe["steps"], [material], transforms)
        debit(state, allowance, slot, f"material-{material}", 1000)
        inventory = state.agents[project["owner"]]["inventory"]
        inventory[f"material-{output}"] = inventory.get(f"material-{output}", 0) + 1000
        current["completed"] = True
        events.append(
            event(
                "resource_transformed",
                dict(
                    project_id=op["project_id"], input=material, output=output, quantity_milli=1000
                ),
                slot,
            )
        )
    return events
