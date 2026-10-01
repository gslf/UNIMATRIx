"""Start the local dashboard or execute an authored benchmark recipe."""

import argparse
import asyncio
import ipaddress
import json
import os
import sys
from pathlib import Path


def cli(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "quality":
        from .research.measurement_quality import main

        return main(argv[1:])
    parser = argparse.ArgumentParser(prog="unimatrix")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("quality", help="Audit and calibrate recipe compression offline")
    serve = commands.add_parser("serve", help="Open the benchmark dashboard server")
    serve.add_argument("--runs-dir", default="runs/benchmarks")
    serve.add_argument("--models-dir", default="config/models")
    serve.add_argument("--recipes-dir", default="config/recipes")
    serve.add_argument("--default-recipe", default="standard-v1")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8001, type=int)
    serve.add_argument("--ssl-certfile")
    serve.add_argument("--ssl-keyfile")
    serve.add_argument("--allowed-host", action="append", default=[])
    serve.add_argument("--auth-token-env", default="UNIMATRIX_DASHBOARD_TOKEN")
    run = commands.add_parser("run", help="Execute every case in an existing benchmark recipe")
    run.add_argument("--model", required=True, help="Candidate model JSON file")
    run.add_argument("--recipe", default="standard-v1", help="Existing benchmark recipe ID")
    run.add_argument("--recipes-dir", default="config/recipes")
    run.add_argument("--runs-dir", default="runs/benchmarks")
    listing = commands.add_parser("recipes", help="List existing benchmark recipes")
    listing.add_argument("--recipes-dir", default="config/recipes")
    estimate = commands.add_parser("estimate", help="Plan a recipe using measured provider latency")
    estimate.add_argument("--recipe", default="standard-v1")
    estimate.add_argument("--recipes-dir", default="config/recipes")
    estimate.add_argument("--seconds-per-decision", required=True, type=float)
    estimate.add_argument("--safety-factor", default=1.5, type=float)
    doctor = commands.add_parser("doctor", help="Check a model endpoint and record runtime provenance")
    doctor.add_argument("--model", required=True)
    doctor.add_argument("--output", required=True)
    replay = commands.add_parser("replay", help="Verify a recorded episode")
    replay.add_argument("--run", required=True, help="Episode folder containing episode.db")
    replay.add_argument("--integrity-only", action="store_true", help="Verify hashes without recomputing transitions")
    backup = commands.add_parser("backup", help="Create and verify a consistent episode SQLite backup")
    backup.add_argument("--run", required=True)
    backup.add_argument("--output", required=True, help="New episode.db path; must not exist")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            import uvicorn

            from .web.server import build_app

            token = os.environ.get(args.auth_token_env)
            try:
                local = ipaddress.ip_address(args.host).is_loopback
            except ValueError:
                local = args.host == "localhost"
            if not local and (not token or len(token) < 32 or not args.allowed_host):
                raise ValueError("remote_dashboard_requires_token_and_explicit_allowed_host")
            if not local and not (args.ssl_certfile and args.ssl_keyfile):
                raise ValueError("remote_dashboard_requires_TLS_certificate_and_key")
            if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
                raise ValueError("TLS_certificate_and_key_must_be_supplied_together")
            hosts = ["localhost", "127.0.0.1", "::1", *args.allowed_host]
            uvicorn.run(
                build_app(args.runs_dir, args.models_dir, args.recipes_dir, args.default_recipe,
                          allowed_hosts=hosts, auth_token=token),
                host=args.host,
                port=args.port,
                proxy_headers=False,
                ssl_certfile=args.ssl_certfile,
                ssl_keyfile=args.ssl_keyfile,
            )
            return 0
        if args.command == "doctor":
            import platform
            from importlib.metadata import version

            from .benchmark.fingerprints import runtime_fingerprint
            from .benchmark.validation import validate_policy
            from .core.ids import digest
            from .web.model_health import check_model

            config = json.loads(Path(args.model).read_text())
            validate_policy(config)
            result = dict(model_configuration_sha256=digest(config), runtime=runtime_fingerprint(),
                          python=platform.python_version(), platform=platform.platform(),
                          lmstudio_sdk=version("lmstudio"),
                          model_sha256_declared=config.get("model_sha256"),
                          health=asyncio.run(check_model(config)))
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf8") as stream:
                json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                stream.write("\n")
        elif args.command in {"replay", "backup"}:
            from .persistence.event_store import EventStore

            store = EventStore(Path(args.run) / "episode.db", read_only=True)
            try:
                from .persistence.replay import backup, replay

                if args.command == "backup":
                    result = backup(store, args.output)
                else:
                    result = store.verify() if args.integrity_only else replay(store)
            finally:
                store.close()
        else:
            from .benchmark.recipes import RecipeRepository

            recipes = RecipeRepository(args.recipes_dir)
            if args.command == "estimate":
                from .benchmark.duration import estimate

                result = estimate(recipes.get(args.recipe), args.seconds_per_decision, args.safety_factor)
            elif args.command == "recipes":
                result = [
                    dict(id=p["id"], name=p["name"], cases=len(p["cases"]),
                         candidate_decisions=len(p["cases"]) * p["ticks"],
                         max_wall_seconds=p.get("max_wall_seconds"))
                    for p in recipes.all().values()
                ]
            else:
                from .benchmark.service import BenchmarkService
                from .benchmark.validation import is_model, validate_policy

                candidate = json.loads(Path(args.model).read_text())
                validate_policy(candidate)
                if not is_model(candidate):
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
                    return service.summary(service.read(run["id"]))

                result = asyncio.run(execute())
        print(json.dumps(result, indent=2))
        if args.command == "doctor":
            return 0 if result["health"]["ok"] else 1
        return 1 if isinstance(result, dict) and result.get("status") in {"failed", "budget_exhausted"} else 0
    except (ValueError, OSError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    sys.exit(cli())
