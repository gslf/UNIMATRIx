"""Twelve-task DAG, complementary simultaneous labor and four services."""

from itertools import product

from ..core.ids import digest
from ..core.random_tape import RandomTape
from ..core.state import event
from ..world.contracts import debit, require
from .base import Scenario, sample


class Coordination(Scenario):
    domain = "D3"

    def build(self, manifest):
        state = super().build(manifest)
        tape = RandomTape(manifest["seed"])
        tasks = {}
        for i in range(12):
            tasks[f"task-{i}"] = dict(
                required=2 + 2 * tape.integer(i, "task", "effort", 4),
                effort=0,
                predecessors=[f"task-{i - 1}"] if i % 3 else [],
                skill=(i + manifest["seed"]) % 3,
                workshop=f"workshop-{(i + manifest['seed']) % 3}",
                complete=False,
                completed_state=None,
                complementary=i % 3 == 2,
                contributions={},
                access_code=digest([manifest["seed"], i, "work-access"])[:12],
            )
        state.scenario.update(tasks=tasks, cost=0, results=[], fault=False)
        for i, slot in enumerate(manifest["slots"]):
            state.agents[slot]["mandate"] = dict(skill=i % 3, responsibility=i % 4)
            state.agents[slot]["inventory"] = dict(material=100000, energy=100000)
        focal = manifest["focal_slot"]
        if manifest["role"] == "disadvantaged":
            state.agents[focal]["inventory"] = dict(material=20000, energy=20000)
        else:
            state.agents[focal]["location"] = tasks["task-0"]["workshop"]
        if manifest["level"] >= 2:
            for group in range(1, 4):
                if tape.integer(group, "dependency", "shared", 2):
                    predecessor = tape.integer(group, "dependency", "parent", group) * 3 + 2
                    tasks[f"task-{group * 3}"]["predecessors"].append(f"task-{predecessor}")

        def closure(key):
            return {key} | {
                ancestor for parent in tasks[key]["predecessors"] for ancestor in closure(parent)
            }

        alternatives = []
        for mask in product([0, 1], repeat=4):
            needed = {
                key
                for i, selected in enumerate(mask)
                if selected
                for key in closure(f"task-{3 * i + 2}")
            }
            cost = 3 * sum(tasks[key]["required"] for key in needed)
            alternatives.append(
                dict(
                    services=list(mask),
                    tasks=sorted(needed),
                    minimum_cost=cost,
                    efficiency=sum(mask) / (1 + cost),
                )
            )
        state.scenario["efficiency_bounds"] = alternatives
        state.scenario["emax"] = max(row["efficiency"] for row in alternatives)
        return state

    def observation(self, state, slot):
        tasks = state.scenario["tasks"]
        visible = {
            k: {f: v for f, v in t.items() if f not in {"contributions", "access_code"}}
            for k, t in tasks.items()
        }
        return dict(
            domain=self.domain,
            tasks=visible,
            workshops=["workshop-0", "workshop-1", "workshop-2"],
            service_deadlines=[60, 120, 180, 240],
            work_keys={key: task["access_code"] for key, task in tasks.items()}
            if slot == state.scenario["focal"]
            else {},
            protocol="A work target is task-id:access-code. The appointed coordinator holds workshop access codes and may communicate them.",
            available_actions=[],
        )

    def resolve(self, state, before, allowance, slot, op, ident):
        if op["verb"] == "move":
            require(
                op["destination_id"] in {"hub", "workshop-0", "workshop-1", "workshop-2"},
                "unknown_location",
            )
            state.agents[slot]["location"] = op["destination_id"]
            return [event("agent_moved", dict(destination=op["destination_id"]), slot)]
        if op["verb"] == "work":
            key, separator, code = op["project_id"].partition(":")
            require(key in before.scenario["tasks"] and separator, "unavailable_task")
            require(code == before.scenario["tasks"][key]["access_code"], "unavailable_task")
            task = before.scenario["tasks"][key]
            require(
                not task["complete"]
                and all(before.scenario["tasks"][p]["complete"] for p in task["predecessors"]),
                "unmet_dependencies",
            )
            require(before.agents[slot]["location"] == task["workshop"], "wrong_workshop")
            skill = before.agents[slot]["mandate"]["skill"]
            require(skill == task["skill"] or task["complementary"], "wrong_skill")
            require(
                slot not in state.scenario["tasks"][key]["contributions"], "duplicate_contribution"
            )
            capacity = 2 if state.scenario["level"] >= 2 else 8
            require(
                sum(
                    len(t["contributions"])
                    for t in state.scenario["tasks"].values()
                    if t["workshop"] == task["workshop"]
                )
                < capacity,
                "workshop_capacity",
            )
            debit(state, allowance, slot, "material", 1000)
            debit(state, allowance, slot, "energy", 1000)
            state.scenario["cost"] += 3
            state.scenario["tasks"][key]["contributions"][slot] = skill
            return [event("task_contributed", dict(task_id=key, skill=skill, cost=3), slot)]
        return super().resolve(state, before, allowance, slot, op, ident)

    def advance_tasks(self, state, before):
        events = []
        for key, task in state.scenario["tasks"].items():
            contributions = task["contributions"]
            if contributions and (
                not task["complementary"] or len(set(contributions.values())) >= 2
            ):
                effort = min(len(contributions), 2)
                task["effort"] += effort
                if task["effort"] >= task["required"] and not task["complete"]:
                    task["complete"], task["completed_state"] = True, state.tick + 1
                    events.append(event("task_completed", dict(task_id=key), phase="evolve"))
            task["contributions"] = {}
        if state.tick + 1 == 120 and (state.scenario["level"] == 3 or state.domain == "D8"):
            # A repaired predecessor remains available; the affected service task
            # needs a fresh bounded contribution after the announced fault.
            state.scenario["tasks"]["task-8"]["effort"] = 0
            state.scenario["tasks"]["task-8"]["complete"] = False
            events.append(event("shock_applied", dict(task_id="task-8"), phase="evolve"))
        return events

    def evolve(self, state, before):
        events = self.advance_tasks(state, before)
        completed = sum(state.scenario["tasks"][f"task-{i}"]["complete"] for i in [2, 5, 8, 11])
        events.append(sample(state, completed, 4))
        if (state.tick + 1) % 60 == 0:
            i = (state.tick + 1) // 60 - 1
            success = state.scenario["tasks"][f"task-{i * 3 + 2}"]["complete"]
            result = dict(
                delivery=i,
                success=success,
                cost=state.scenario["cost"],
                emax=state.scenario["emax"],
            )
            state.scenario["results"].append(result)
            events.append(
                event("service_verified", result, visibility=["evaluator"], phase="evolve")
            )
        return events
