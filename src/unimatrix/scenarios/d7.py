"""Procedural transfer on disjoint inputs and a fresh apprentice at S120."""

from itertools import permutations

from ..core.random_tape import RandomTape
from ..core.state import event
from ..core.visibility import allowed
from ..world.contracts import debit, require
from ..world.recipes import execute
from .base import Scenario, replace_slot


class Transmission(Scenario):
    domain = "D7"

    def build(self, manifest):
        state = super().build(manifest)
        tape = RandomTape(manifest["seed"])
        focal = manifest["focal_slot"]
        learner = next(s for s in manifest["slots"] if s != focal)
        names = [f"transform-{i}-{tape.integer(i, 'name', 'alias', 1000000)}" for i in range(3)]
        permutations_ = list(permutations(range(4)))
        transforms = {
            name: list(permutations_[tape.integer(i, name, "mapping", len(permutations_))])
            for i, name in enumerate(names)
        }
        procedures = {
            f"procedure-{i}": (
                [names[i % 3]]
                if i < 3 and manifest["level"] == 1
                else [names[i % 3], names[(i // 3 + i + 1) % 3]]
            )
            for i in range(6)
        }
        if manifest["level"] == 3:
            procedures = {
                key: chain + [names[(i + 2) % 3]]
                for i, (key, chain) in enumerate(procedures.items())
            }
        tasks = []
        for i in range(15):
            kind = "transfer" if i < 6 else "reuse" if i < 12 else "retention"
            index = i if i < 6 else i - 6 if i < 12 else i - 12
            due = (
                [40, 80, 100, 160, 200, 240][index]
                if kind != "retention"
                else [160, 200, 240][index]
            )
            chain = procedures[f"procedure-{index}"]
            value = 1 if kind == "transfer" else 2 if kind == "reuse" else 3
            target = value
            for name in chain:
                target = transforms[name][target]
            tasks.append(
                dict(
                    id=f"{kind}-{index}",
                    kind=kind,
                    input=value,
                    chain=chain,
                    procedure_id=f"procedure-{index}",
                    target=target,
                    due=due,
                    open=max(0, due - 19),
                    success=False,
                    artifact=None,
                )
            )
        state.scenario.update(
            transforms=transforms, procedures=procedures, tasks=tasks, learner=learner, results=[]
        )
        for slot in state.agents:
            state.agents[slot]["inventory"] = dict(
                energy=100000, **{f"material-{i}": 10000 for i in range(4)}
            )
        return state

    def observation(self, state, slot):
        s = state.scenario
        tasks = [
            {k: v for k, v in task.items() if k not in {"target", "success", "artifact", "chain"}}
            for task in s["tasks"]
            if task["open"] <= state.tick < task["due"]
        ]
        # The teacher sees examples and the grammar, never heldout input/output pairs.
        return dict(
            domain=self.domain,
            learner=s["learner"],
            transforms=s["transforms"] if slot == s["focal"] else None,
            procedures=s["procedures"] if slot == s["focal"] else None,
            tasks=tasks if slot == s["learner"] else [],
            training_inputs=[0],
            available_actions=[],
        )

    def resolve(self, state, before, allowance, slot, op, ident):
        s = state.scenario
        if op["verb"] == "register_recipe":
            execute(op["steps"], [0], s["transforms"])
            state.objects[ident] = dict(
                kind="recipe",
                owner=slot,
                visibility=[slot],
                name=op["name"],
                steps=op["steps"],
                verified=False,
            )
            return [event("recipe_registered", dict(recipe_id=ident), slot, [slot])]
        if op["verb"] == "experiment":
            recipe = before.objects.get(op["recipe_id"], {})
            require(recipe.get("kind") == "recipe" and allowed(recipe, slot), "unavailable_recipe")
            ids = op["input_asset_ids"]
            if ids == ["training-0"]:
                output = execute(recipe["steps"], [0], s["transforms"])
                debit(state, allowance, slot, "energy", 1000)
                state.objects[op["recipe_id"]]["verified"] = True
                return [
                    event(
                        "experiment_result",
                        dict(recipe_id=op["recipe_id"], input=0, output=output),
                        slot,
                        [slot],
                    )
                ]
            require(
                slot == s["learner"] and len(ids) == 1 and recipe["verified"],
                "verified_recipe_required",
            )
            task = next((t for t in s["tasks"] if t["id"] == ids[0]), None)
            require(
                task is not None
                and task["open"] <= state.tick < task["due"]
                and not task.get("submitted", False),
                "unavailable_task",
            )
            output = execute(recipe["steps"], [task["input"]], s["transforms"])
            debit(state, allowance, slot, "energy", 1000)
            artifact = next(
                (
                    key
                    for key, obj in before.objects.items()
                    if obj.get("kind") == "artifact"
                    and obj["owner"] == s["focal"]
                    and slot in obj["read_by"]
                    and allowed(obj, slot)
                    and all(step["transform_id"] in obj["content"] for step in recipe["steps"])
                ),
                None,
            )
            reusable = recipe.get("owner") == s["focal"] if task["kind"] == "reuse" else True
            if task["kind"] == "reuse":
                expected = []
                for value in range(4):
                    target = value
                    for transform in task["chain"]:
                        target = s["transforms"][transform][target]
                    expected.append(target)
                reusable = (
                    reusable
                    and [execute(recipe["steps"], [value], s["transforms"]) for value in range(4)]
                    == expected
                )
            task["success"] = output == task["target"] and artifact is not None and reusable
            task["artifact"] = artifact
            task["submitted"] = True
            return [
                event(
                    "recipe_executed",
                    dict(
                        task_id=task["id"],
                        recipe_id=op["recipe_id"],
                        output=output,
                        artifact_id=artifact,
                    ),
                    slot,
                    [slot],
                )
            ]
        return super().resolve(state, before, allowance, slot, op, ident)

    def evolve(self, state, before):
        events = []
        for task in state.scenario["tasks"]:
            if task["due"] == state.tick + 1:
                result = dict(
                    task_id=task["id"],
                    kind=task["kind"],
                    success=task["success"],
                    artifact_id=task["artifact"],
                )
                state.scenario["results"].append(result)
                events.append(
                    event("heldout_task_resolved", result, visibility=["evaluator"], phase="evolve")
                )
        if state.tick + 1 == 120:
            learner = state.scenario["learner"]
            events.append(replace_slot(state, learner))
            for obj in state.objects.values():
                if obj.get("kind") == "artifact" and learner in obj.get("read_by", []):
                    obj["read_by"].remove(learner)
            state.objects = {
                k: v
                for k, v in state.objects.items()
                if not (v.get("owner") == learner and "public" not in v.get("visibility", []))
            }
        return events
