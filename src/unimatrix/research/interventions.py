"""Candidate-only interventions with actual provider input/output retained as evidence."""

from copy import deepcopy

from jsonschema import ValidationError

from ..actions.schemas import validate
from ..core.ids import canonical, digest
from ..core.visibility import bound_packet
from ..policies.ablations import rename


class IntervenedPolicy:
    def __init__(self, policy, intervention, research_runtime):
        self.policy = policy
        self.intervention = intervention
        self.fingerprint = digest([policy.fingerprint, intervention, research_runtime])

    async def decide(self, observation, budget):
        packet = deepcopy(observation)
        variant = self.intervention
        if variant == "no_memory":
            packet.update(private_note="", retrieval=[], events=[])
        if variant == "no_communication":
            packet["inbox"] = []
            for field in ["retrieval", "events"]:
                packet[field] = [
                    r
                    for r in packet[field]
                    if r.get("type") != "message_sent" and not ("sender" in r and "content" in r)
                ]
            packet["objects"] = {
                key: obj
                for key, obj in packet["objects"].items()
                if obj.get("kind") != "artifact" or obj.get("owner") == packet["agent_id"]
            }
        aliases = {}
        if variant == "rename_agents":
            aliases = {p["id"]: "person-" + digest(p["id"])[:8] for p in packet["peers"]}
            packet = rename(packet, aliases)
        if variant == "reverse_observation_order":
            packet["peers"].reverse()
        packet = bound_packet(packet)
        raw, usage = await self.policy.decide(packet, budget)
        usage = dict(
            usage, research_intervention=variant, research_input=packet, research_output=raw
        )
        try:

            decision = validate(raw, packet["tick"], packet["agent_id"])
            if variant == "no_communication":
                decision["messages"] = []
                ops = decision.get("operations")
                if isinstance(ops, list) and all(isinstance(op, dict) for op in ops):
                    decision["operations"] = [
                        op
                        for op in ops
                        if op.get("verb") not in {"publish", "teach", "grant_access", "handover"}
                    ]
            if variant == "no_memory":
                decision.update(private_note="", memory_query=None)
            return canonical(
                rename(decision, {value: key for key, value in aliases.items()})
            ), usage
        except (ValueError, TypeError, ValidationError):

            return raw, usage

    async def close(self):
        if hasattr(self.policy, "close"):
            await self.policy.close()
