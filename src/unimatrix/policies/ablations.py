"""Research interventions have distinct manifests and fingerprints."""

import json

from ..core.ids import canonical, digest

INTERVENTIONS = {
    "no_communication",
    "no_memory",
    "rename_agents",
    "reverse_observation_order",
}


def rename(value, aliases):
    if isinstance(value, dict):
        return {aliases.get(k, k): rename(v, aliases) for k, v in value.items()}
    if isinstance(value, list):
        return [rename(v, aliases) for v in value]
    return aliases.get(value, value) if isinstance(value, str) else value


class Ablated:
    def __init__(self, policy, interventions):
        if set(interventions) - INTERVENTIONS:
            raise ValueError("unknown_ablation")
        self.policy, self.interventions = policy, interventions
        self.fingerprint = digest([policy.fingerprint, interventions])

    async def decide(self, observation, budget):
        observation = json.loads(canonical(observation))
        if "no_communication" in self.interventions:
            observation["inbox"] = []
            observation["objects"] = {
                key: obj
                for key, obj in observation["objects"].items()
                if obj.get("kind") != "artifact" or obj.get("owner") == observation["agent_id"]
            }
        if "no_memory" in self.interventions:
            observation["private_note"] = ""
            observation["retrieval"] = []
            observation["events"] = []
        aliases = {}
        if "rename_agents" in self.interventions:
            aliases = {
                peer["id"]: "person-" + digest(peer["id"])[:8] for peer in observation["peers"]
            }
            observation = rename(observation, aliases)
        if "reverse_observation_order" in self.interventions:
            observation["peers"].reverse()
        from ..core.visibility import bound_packet

        observation = bound_packet(observation)
        raw, usage = await self.policy.decide(observation, budget)
        try:
            decision = json.loads(raw)
            if not isinstance(decision, dict):
                return raw, usage
            if "no_communication" in self.interventions:
                decision["messages"] = []
                decision["operations"] = [
                    op
                    for op in decision.get("operations", [])
                    if op.get("verb") not in {"publish", "teach", "grant_access", "handover"}
                ]
            if "no_memory" in self.interventions:
                decision["private_note"] = ""
                decision["memory_query"] = None
            return canonical(
                rename(decision, {value: key for key, value in aliases.items()})
            ), usage
        except ValueError:
            return raw, usage

    async def close(self):
        if hasattr(self.policy, "close"):
            await self.policy.close()
