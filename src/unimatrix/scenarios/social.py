"""Open material society, using the same actions, barriers and event store as Core."""

from ..core.ids import digest
from ..core.state import agent, event
from ..world.contracts import debit, require
from ..world.recipes import execute
from .base import Scenario


class SocialWorld(Scenario):
    domain = "social"

    def build(self, manifest):
        state = super().build(manifest)
        state.scenario.update(
            stocks=dict(food=100000, water=100000, material=100000),
            needs={},
            births=0,
            deaths=0,
            transforms={"press": [1, 2, 3, 0], "refine": [2, 0, 3, 1]},
        )
        for i, slot in enumerate(manifest["slots"]):
            living = i < manifest["initial_population"]
            state.agents[slot].update(
                alive=living,
                health=10000 if living else 0,
                age=0,
                nutrition=dict(food=1000, water=1000),
                inventory=dict(
                    food=20000,
                    water=20000,
                    material=10000,
                    energy=10000,
                    **{f"material-{j}": 2000 for j in range(4)},
                )
                if living
                else {},
            )
            for resource in ("food", "water", "material"):
                state.objects[f"asset-{slot}-{resource}"] = dict(
                    kind="asset",
                    owner=slot,
                    visibility=["public"],
                    resource=resource,
                )
        return state

    def feasible(self, manifest):
        state = self.build(manifest)
        require(
            2 <= manifest["initial_population"] <= len(state.agents) <= 16, "invalid_population"
        )
        require(
            all(q >= 0 for a in state.agents.values() for q in a["inventory"].values()),
            "invalid_endowment",
        )
        return dict(
            valid=True,
            proof_kind="bounded_material_system",
            instance_hash=digest(state.dump()),
            capacity=len(state.agents),
            horizon=manifest["ticks"],
        )

    def observation(self, state, slot):
        return dict(
            domain=self.domain,
            horizon=state.scenario["horizon"],
            stocks=state.scenario["stocks"],
            transforms=state.scenario["transforms"],
            vacant_slots=[s for s, a in state.agents.items() if not a["alive"]],
            mechanics={
                "survival": "Nutrition uses 100 milliunits of food and water per tick; consume refills each internal buffer up to 1000. Missing either costs 1000 health. Metabolism and age are material; a profile cannot override them.",
                "work": "At food, water or material location, work on harvest-RESOURCE costs 1000 energy and collects 1000 milliunits; move costs one operation.",
                "recovery": "Unused energy regenerates 500 per tick up to 10000. Adequate nutrition restores 100 health. Maximum age is 480 ticks.",
                "birth": "Two living parents sign identical terms for a vacant slot. Each pays cost_milli of food, water and material. The child gets half the combined inputs; the rest is consumed. No inherited private memory. Shared generation budget does not grow.",
                "recipes": "Register an acyclic recipe and experiment with training-0 before creating a material project.",
            },
        )

    def resolve(self, state, before, allowance, slot, op, ident):
        verb = op["verb"]
        if verb == "move":
            require(
                op["destination_id"] in {"hub", "food", "water", "material"}, "unknown_location"
            )
            state.agents[slot]["location"] = op["destination_id"]
            return [event("agent_moved", dict(destination=op["destination_id"]), slot)]
        if verb == "consume":
            resource = op["resource_id"]
            require(resource in {"food", "water"}, "not_nutrition")
            debit(state, allowance, slot, resource, op["quantity_milli"])
            needs = state.scenario["needs"].setdefault(slot, {})
            needs[resource] = needs.get(resource, 0) + op["quantity_milli"]
            return [
                event(
                    "resource_consumed",
                    dict(resource=resource, quantity_milli=op["quantity_milli"]),
                    slot,
                )
            ]
        if verb == "work":
            resource = op["project_id"].removeprefix("harvest-")
            require(
                op["project_id"].startswith("harvest-") and resource in state.scenario["stocks"],
                "unknown_work",
            )
            require(before.agents[slot]["location"] == resource, "wrong_location")
            require(state.scenario["stocks"][resource] >= 1000, "stock_exhausted")
            debit(state, allowance, slot, "energy", 1000)
            state.scenario["stocks"][resource] -= 1000
            state.agents[slot]["inventory"][resource] += 1000
            return [
                event(
                    "resource_extracted",
                    dict(resource=resource, quantity_milli=1000, energy_consumed_milli=1000),
                    slot,
                )
            ]
        if verb == "register_recipe":
            execute(op["steps"], [0], state.scenario["transforms"])
            state.objects[ident] = dict(
                kind="recipe",
                owner=slot,
                visibility=[slot],
                name=op["name"],
                steps=op["steps"],
                verified=False,
            )
            return [event("recipe_registered", dict(recipe_id=ident), slot, [slot])]
        if verb == "experiment":
            recipe = before.objects.get(op["recipe_id"], {})
            require(
                recipe.get("owner") == slot and recipe.get("kind") == "recipe", "unavailable_recipe"
            )
            require(op["input_asset_ids"] == ["training-0"], "unknown_training_input")
            debit(state, allowance, slot, "energy", 1000)
            output = execute(recipe["steps"], [0], state.scenario["transforms"])
            state.objects[op["recipe_id"]]["verified"] = True
            return [
                event(
                    "experiment_result",
                    dict(recipe_id=op["recipe_id"], output=output),
                    slot,
                    [slot],
                )
            ]
        if verb == "propose_birth":
            partner, child, cost = op["partner_id"], op["child_slot"], op["cost_milli"]
            require(
                partner != slot and partner in before.agents and before.agents[partner]["alive"],
                "invalid_partner",
            )
            require(child in before.agents and not before.agents[child]["alive"], "no_vacant_slot")
            require(
                state.tick + 2 <= op["expiry_state"] <= self.deadline(state), "invalid_deadline"
            )
            terms = dict(
                parents=[slot, partner],
                child_slot=child,
                cost_milli=cost,
                expiry_state=op["expiry_state"],
            )
            state.objects[ident] = dict(
                kind="birth_proposal",
                owner=slot,
                visibility=[slot, partner],
                terms=terms,
                terms_hash=digest(terms),
                status="open",
            )
            return [
                event(
                    "birth_proposed",
                    dict(proposal_id=ident, terms_hash=digest(terms)),
                    slot,
                    [slot, partner],
                )
            ]
        if verb == "accept_birth":
            proposal = before.objects.get(op["proposal_id"], {})
            require(
                proposal.get("kind") == "birth_proposal"
                and state.objects[op["proposal_id"]]["status"] == "open",
                "unavailable_proposal",
            )
            terms = proposal["terms"]
            require(
                slot == terms["parents"][1] and op["terms_hash"] == proposal["terms_hash"],
                "invalid_signature",
            )
            require(state.tick + 1 <= terms["expiry_state"], "expired_proposal")
            child, cost = terms["child_slot"], terms["cost_milli"]
            require(not state.agents[child]["alive"], "no_vacant_slot")
            require(
                not any(
                    obj.get("kind") == "offer"
                    and child in obj.get("visibility", [])
                    and obj["status"] in {"open", "accepted"}
                    for obj in state.objects.values()
                ),
                "predecessor_estate_encumbered",
            )
            for parent in terms["parents"]:
                require(before.agents[parent]["alive"], "parent_unavailable")
                for resource in ["food", "water", "material"]:
                    debit(state, allowance, parent, resource, cost)
            old = state.agents[child]
            newborn = agent(child, dict(food=cost, water=cost, material=cost, energy=10000))
            newborn.update(
                generation=old["generation"] + 1,
                health=10000,
                age=0,
                parents=terms["parents"],
                nutrition=dict(food=0, water=0),
            )
            state.agents[child] = newborn
            state.objects[op["proposal_id"]]["status"] = "accepted"
            state.scenario["births"] += 1
            return [
                event(
                    "agent_born",
                    dict(
                        slot=child,
                        parents=terms["parents"],
                        generation=newborn["generation"],
                        consumed_per_resource_milli=cost,
                    ),
                    slot,
                )
            ]
        return super().resolve(state, before, allowance, slot, op, ident)

    def evolve(self, state, before):
        events = []
        for slot, current in state.agents.items():
            if not current["alive"]:
                continue
            if current["generation"] != before.agents[slot]["generation"]:
                state.inbox[slot], state.memories[slot], state.receipts[slot] = [], [], []
                state.objects = {
                    k: v
                    for k, v in state.objects.items()
                    if not (v.get("owner") == slot and "public" not in v.get("visibility", []))
                }
                for obj in state.objects.values():
                    if "public" not in obj.get("visibility", []) and slot in obj.get(
                        "visibility", []
                    ):
                        obj["visibility"].remove(slot)
                    if obj.get("kind") == "artifact" and slot in obj.get("read_by", []):
                        obj["read_by"].remove(slot)
                    if obj.get("kind") == "rule" and slot in obj.get("voters", []):
                        obj["status"] = "invalidated"
                    if obj.get("kind") == "group" and slot in obj["members"]:
                        obj["members"].remove(slot)
                    if obj.get("kind") == "delegation" and slot in [
                        obj["owner"],
                        obj["recipient_id"],
                    ]:
                        obj["active"] = False
                continue
            nutrition = state.scenario["needs"].get(slot, {})
            buffer = current["nutrition"]
            for resource in ["food", "water"]:
                buffer[resource] = min(1000, buffer.get(resource, 0) + nutrition.get(resource, 0))
            adequate = all(buffer[r] >= 100 for r in buffer)
            for resource in buffer:
                buffer[resource] = max(0, buffer[resource] - 100)
            current["age"] += 1
            current["health"] = min(10000, max(0, current["health"] + (100 if adequate else -1000)))
            initial = current["inventory"].get("energy", 0)
            current["inventory"]["energy"] = min(10000, initial + 500)
            events.append(
                event(
                    "metabolism",
                    dict(
                        adequate=adequate,
                        health=current["health"],
                        energy_generated_milli=current["inventory"]["energy"] - initial,
                    ),
                    slot,
                    [slot],
                    "evolve",
                )
            )
            if current["health"] == 0 or current["age"] >= 480:
                current["alive"] = False
                state.scenario["deaths"] += 1
                events.append(
                    event(
                        "agent_died",
                        dict(slot=slot, generation=current["generation"]),
                        phase="evolve",
                    )
                )
        state.scenario["needs"] = {}
        for resource, quantity in state.scenario["stocks"].items():
            growth = min(500, 100000 - quantity)
            state.scenario["stocks"][resource] += growth
            events.append(
                event(
                    "resource_regenerated",
                    dict(resource=resource, quantity_milli=growth),
                    phase="evolve",
                )
            )
        for obj in state.objects.values():
            if (
                obj.get("kind") == "birth_proposal"
                and obj["status"] == "open"
                and obj["terms"]["expiry_state"] <= state.tick + 1
            ):
                obj["status"] = "expired"
        return events
