"""Create a society run driven by a scripted steward through the real runner and service.

Usage: uv run python tests/tools/fake_society_run.py [runs_dir] [policy] [--partial WEEKS]
(finished runs are exported to explorer/data, which Git ignores)
The run appears in the dashboard like a model run (model "scripted-<policy>").
"""

import asyncio
import json
import sys
from pathlib import Path

from unimatrix.core.ids import canonical
from unimatrix.polis.baselines import make
from unimatrix.polis.recipe import PolisRecipes
from unimatrix.polis.service import SocietyService

CONFIG = {"model": "scripted", "snapshot": "reference", "endpoint": "http://127.0.0.1:9",
          "budget_track": "opaque_compute", "context_bytes_verified": 24000}


class ScriptedPolicy:
    def __init__(self, name, stop=None):
        self.name, self.stop, self.steward, self.calls = name, stop, None, 0

    async def decide(self, observation):
        if self.steward is None:
            self.steward = make(self.name if self.name != "oracle" else "expert")
        self.calls += 1
        if self.stop and self.calls > self.stop:
            await asyncio.sleep(3600)
        decision = self.steward.decide(observation)
        return canonical(decision), dict(input_tokens=2500, generated_tokens=400)

    async def close(self):
        pass


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    runs = Path(args[0] if args else "runs")
    policy = args[1] if len(args) > 1 else "expert"
    partial = int(sys.argv[sys.argv.index("--partial") + 1]) if "--partial" in sys.argv else None
    recipes = PolisRecipes("config/societies")
    recipe = recipes.get("standard-v2")
    service = SocietyService(runs, recipes, explorer="explorer")
    import unimatrix.polis.llm as llm

    llm.StewardPolicy = lambda config, recipe: ScriptedPolicy(policy, partial)
    run = await service.start(dict(CONFIG, model=f"scripted-{policy}"), f"scripted-{policy}", recipe)
    task = service.active[run["id"]][0]
    if partial:
        while service.read(run["id"])["seeds"][str(recipe["seeds"][0])]["week"] < partial:
            await asyncio.sleep(0.05)
        await service.pause(run["id"])
    else:
        await task
    print(json.dumps(service.summary(service.read(run["id"])), indent=1)[:600])


if __name__ == "__main__":
    asyncio.run(main())
