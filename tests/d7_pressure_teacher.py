"""Observation-only constructive instructor for deterministic D7 pressure checks."""

from unimatrix.actions.schemas import empty
from unimatrix.core.ids import canonical


class CurriculumTeacher:
    def __init__(self, redundant=False):
        self.redundant = redundant
        self.fingerprint = "d7-curriculum-pressure-v1:" + str(redundant)

    async def decide(self, observation, budget):
        o = observation
        s, slot, objects = o["scenario"], o["agent_id"], o["objects"]
        learner = s["learner"]
        output = empty(o["tick"], slot)
        ops, registered = [], []
        for name, chain in sorted(s["procedures"].items()):
            steps = [dict(transform_id=t, input_slots=[i], output_slot=i+1) for i, t in enumerate(chain)]
            item = next(((k, v) for k, v in objects.items() if v.get("kind") == "recipe"
                         and v["owner"] == slot and v["name"] == name and v["steps"] == steps), None)
            if item is None:
                ops.append(dict(verb="register_recipe", name=name, steps=steps))
            else:
                registered.append(item)
        if not ops:
            ops = [dict(verb="experiment", recipe_id=k, input_asset_ids=["training-0"])
                   for k, v in registered if not v["verified"]]
        content = canonical(dict(procedures=s["procedures"]))
        publication = dict(verb="publish", kind="procedure", content=content, parent_ids=[])
        manuals = [(k, v) for k, v in objects.items() if v.get("kind") == "artifact"
                   and v["owner"] == slot and v["content"] == content]
        if not ops and not manuals:
            ops = [publication]
        if not ops:
            ops = [dict(verb="grant_access", object_id=k, recipient_id=learner)
                   for k, v in registered if learner not in v["visibility"]]
        if not ops and s.get("teaching_required") and not any(learner in v.get("taught_to", []) for _, v in manuals):


            target = min(manuals, key=lambda item: (learner not in item[1]["read_by"], item[0]))[0]
            ops = [dict(verb="teach", recipient_id=learner, artifact_id=target)]
        ready = (len(registered) == len(s["procedures"]) and manuals
                 and all(v["verified"] and learner in v["visibility"] for _, v in registered))
        if self.redundant and ready and len(ops) < 2:
            ops.append(publication)
        output["operations"] = ops[:2]
        return canonical(output), dict(generated_tokens=0, purpose="decision")

    async def close(self):
        pass
