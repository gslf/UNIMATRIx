"""Scenario extension points; private evaluator data stays behind observation()."""

from ..core.ids import digest
from ..core.state import WorldState, agent, event


class Scenario:
    domain = ""

    def build(self, manifest):
        state = WorldState(manifest["run_id"], manifest["seed"], self.domain)
        for slot in manifest["slots"]:
            state.agents[slot] = agent(slot)
            state.inbox[slot], state.memories[slot], state.receipts[slot] = [], [], []
        state.scenario = dict(
            level=manifest["level"],
            focal=manifest["focal_slot"],
            role=manifest["role"],
            samples=[],
            mode=manifest["mode"],
            horizon=manifest["ticks"],
        )
        return state

    def observation(self, state, slot):
        return dict(domain=self.domain, level=state.scenario["level"])

    def deadline(self, state):
        return state.scenario["horizon"]

    def validate_transfer(self, state, slot, op):
        """Ordinary owned resources are transferable unless a scenario restricts them."""

    def resolve(self, state, before, allowance, slot, op, ident):
        from ..world.contracts import Rejected

        raise Rejected("unsupported_operation")

    def evolve(self, state, before):
        return []

    def forecasts(self, state, before, slot, forecasts):
        return []

    def feasible(self, manifest):
        state = self.build(manifest)
        from ..world.contracts import require

        require(
            8 <= len(state.agents) <= 128 and state.tick == 0 and manifest["ticks"] == 240,
            "invalid_world_shape",
        )
        require(
            all(
                type(q) is int and q >= 0
                for a in state.agents.values()
                for q in a["inventory"].values()
            ),
            "invalid_endowment",
        )
        data = state.dump()
        data.pop("run_id")
        from ..benchmark.feasibility import certify

        return dict(certify(manifest), instance_hash=digest(data))


def sample(state, value, target=1):
    state.scenario["samples"].append(min(max(value / target, 0), 1))
    return event(
        "service_sampled",
        dict(value=value, target=target),
        visibility=["evaluator"],
        phase="evolve",
    )


def replace_slot(state, slot):
    old = state.agents[slot]
    state.agents[slot] = agent(slot, old["inventory"], old["mandate"])
    state.agents[slot]["generation"] = old["generation"] + 1
    state.inbox[slot], state.memories[slot], state.receipts[slot] = [], [], []
    for obj in state.objects.values():
        if (
            "public" not in obj.get("visibility", [])
            and slot in obj.get("visibility", [])
            and obj.get("owner") != slot
        ):
            obj["visibility"].remove(slot)
        if obj.get("kind") == "artifact" and slot in obj.get("read_by", []):
            obj["read_by"].remove(slot)
        if obj.get("kind") == "rule" and slot in obj.get("voters", []):
            obj["status"] = "invalidated"
        if obj.get("kind") == "delegation" and slot in [obj["owner"], obj["recipient_id"]]:
            obj["active"] = False
        if obj.get("kind") == "group" and slot in obj["members"]:
            obj["members"].remove(slot)
    return event("slot_replaced", dict(slot=slot, generation=old["generation"] + 1), phase="evolve")
