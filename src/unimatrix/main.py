"""Start the local dashboard or execute an authored benchmark recipe."""

import argparse
import asyncio
import json
import sys
from pathlib import Path


def cli(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Keep the bench spelling as an alias, without the retired planning commands.
    if argv and argv[0] == "bench":
        argv = argv[1:]
    # Accept previous command spellings without advertising them in the help text.
    if argv and argv[0] == "plans":
        argv[0] = "recipes"
    aliases = {
        "--plan": "--recipe",
        "--plans-dir": "--recipes-dir",
        "--default-plan": "--default-recipe",
    }
    argv = [
        aliases.get(arg.split("=", 1)[0], arg.split("=", 1)[0])
        + ("=" + arg.split("=", 1)[1] if "=" in arg else "")
        for arg in argv
    ]
    parser = argparse.ArgumentParser(prog="unimatrix")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Open the benchmark dashboard server")
    serve.add_argument("--runs-dir", default="runs/benchmarks")
    serve.add_argument("--models-dir", default="config/models")
    serve.add_argument("--recipes-dir", default="config/recipes")
    serve.add_argument("--default-recipe", default="standard-v1")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8001, type=int)
    run = commands.add_parser("run", help="Execute every case in an existing benchmark recipe")
    run.add_argument("--model", required=True, help="Candidate model JSON file")
    run.add_argument("--recipe", default="standard-v1", help="Existing benchmark recipe ID")
    run.add_argument("--recipes-dir", default="config/recipes")
    run.add_argument("--runs-dir", default="runs/benchmarks")
    listing = commands.add_parser("recipes", help="List existing benchmark recipes")
    listing.add_argument("--recipes-dir", default="config/recipes")
    replay = commands.add_parser("replay", help="Verify a recorded episode")
    replay.add_argument("--run", required=True, help="Episode folder containing episode.db")
    replay.add_argument("--verify-hashes", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            import uvicorn

            from .web.server import build_app

            uvicorn.run(
                build_app(args.runs_dir, args.models_dir, args.recipes_dir, args.default_recipe),
                host=args.host,
                port=args.port,
            )
            return 0
        if args.command == "replay":
            from .persistence.event_store import EventStore

            store = EventStore(Path(args.run) / "episode.db", read_only=True)
            try:
                result = store.verify()
            finally:
                store.close()
        else:
            from .benchmark.recipes import RecipeRepository, recipe_fields

            recipes = RecipeRepository(args.recipes_dir)
            if args.command == "recipes":
                result = [
                    dict(id=p["id"], name=p["name"], cases=len(p["cases"]))
                    for p in recipes.all().values()
                ]
            else:
                from .benchmark.service import BenchmarkService
                from .benchmark.validation import validate_policy

                candidate = json.loads(Path(args.model).read_text())
                validate_policy(candidate)
                if not isinstance(candidate, dict):
                    raise ValueError("--model requires a candidate model JSON object")

                async def execute():
                    service = BenchmarkService(args.runs_dir, recipes)
                    run = await service.start(
                        candidate, Path(args.model).stem, recipes.get(args.recipe)
                    )
                    try:
                        await service.active[run["id"]][0]
                    finally:
                        await service.shutdown()
                    return recipe_fields(service.summary(service.read(run["id"])))

                result = asyncio.run(execute())
        print(json.dumps(result, indent=2))
        return 1 if isinstance(result, dict) and result.get("status") == "failed" else 0
    except (ValueError, OSError, TypeError) as error:
        from .benchmark.recipes import recipe_text

        parser.error(recipe_text(str(error)))


if __name__ == "__main__":
    sys.exit(cli())
