"""UNIMATRIx command line: serve the dashboard, run a model as steward, preview recipes."""

import argparse
import asyncio
import ipaddress
import json
import os
import sys
from pathlib import Path


def load_recipe(recipes, value):
    """A recipe id, or a path to an authored recipe JSON file."""
    from .polis.recipe import normalize

    path = Path(value)
    if path.suffix == ".json" and path.is_file():
        return normalize(json.loads(path.read_text(encoding="utf8")))
    return recipes.get(value)


def parser():
    root = argparse.ArgumentParser(prog="unimatrix")
    root.add_argument("--societies-dir", default="config/societies", help="Authored recipes (git-ignored)")
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Open the dashboard: home, Society Lab and run reports")
    serve.add_argument("--runs-dir", default="runs")
    serve.add_argument("--models-dir", default="config/models")
    serve.add_argument("--explorer-dir", default="explorer",
                       help="Folder of the static Explorer; finished runs are exported there as xCDN")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8001, type=int)
    serve.add_argument("--ssl-certfile")
    serve.add_argument("--ssl-keyfile")
    serve.add_argument("--allowed-host", action="append", default=[])
    serve.add_argument("--auth-token-env", default="UNIMATRIX_DASHBOARD_TOKEN")
    commands.add_parser("recipes", help="List recipes")
    run = commands.add_parser("run", help="Run a candidate model as steward")
    run.add_argument("--model", required=True, help="Candidate model JSON file")
    run.add_argument("--recipe", default="standard-v2", help="Recipe id or JSON file")
    run.add_argument("--runs-dir", default="runs")
    run.add_argument("--parallelism", type=int, default=1, help="Seeds played side by side")
    run.add_argument("--explorer-dir", default="explorer", help="Where the run's xCDN file for the Explorer goes")
    sim = commands.add_parser("simulate", help="Play a recipe with a scripted reference steward")
    sim.add_argument("--recipe", default="standard-v2")
    sim.add_argument("--policy", default="expert", choices=["passive", "random", "naive", "expert", "oracle"])
    sim.add_argument("--seed", type=int)
    sim.add_argument("--output", help="Write the full report (with frames) to this JSON file")
    cal = commands.add_parser("calibrate", help="Score every reference steward on several seeds")
    cal.add_argument("--recipe", default="standard-v2")
    cal.add_argument("--seeds", default="1,2,3,4,5")
    cal.add_argument("--target", type=float, default=1000.0, help="Expert points per domain for suggested rates")
    est = commands.add_parser("estimate", help="Wall-clock estimate from a measured latency")
    est.add_argument("--recipe", default="standard-v2")
    est.add_argument("--seconds-per-decision", type=float, required=True)
    rep = commands.add_parser("replay", help="Rebuild a recorded seed without calling the model")
    rep.add_argument("--run", required=True, help="Run folder (contains run.json)")
    rep.add_argument("--seed", type=int, required=True)
    exp = commands.add_parser("export", help="Export finished runs as xCDN files for the Explorer")
    exp.add_argument("--runs-dir", default="runs")
    exp.add_argument("--explorer-dir", default="explorer")
    exp.add_argument("--run", help="Only this run id")
    demo = commands.add_parser("demo", help="Export the scripted reference stewards of a recipe to the Explorer")
    demo.add_argument("--recipe", default="standard-v2")
    demo.add_argument("--explorer-dir", default="explorer")
    doctor = commands.add_parser("doctor", help="Check a model endpoint and record runtime provenance")
    doctor.add_argument("--model", required=True)
    doctor.add_argument("--output", required=True)
    return root


def serve(args):
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
        build_app(args.runs_dir, args.models_dir, args.societies_dir, args.explorer_dir,
                  allowed_hosts=hosts, auth_token=token),
        host=args.host, port=args.port, proxy_headers=False,
        ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile,
    )


def execute(args):
    from .polis.recipe import PolisRecipes, summary

    recipes = PolisRecipes(args.societies_dir)
    if args.command == "recipes":
        return [summary(r) for r in recipes.all().values()]
    if args.command == "simulate":
        from .polis.simulate import simulate

        report = simulate(load_recipe(recipes, args.recipe), args.seed, args.policy, trace=bool(args.output))
        if args.output:
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False), encoding="utf8")
        return dict(score=report["score"], narrative=report["narrative"],
                    diagnosis=[f"[{d['level']}] {d['title']}: {d['text']}" for d in report["diagnosis"]],
                    path=[f"wk{p['week']} {p['source']} → {p['target']}" for p in report["story"]["path"]])
    if args.command == "calibrate":
        from .polis.simulate import calibrate, suggested_rates

        recipe = load_recipe(recipes, args.recipe)
        rows = calibrate(recipe, [int(s) for s in args.seeds.split(",")])
        return dict(rows=[dict(policy=r["policy"], seed=r["seed"], total=r["total"], earned=r["earned"],
                               collapsed=(r["collapsed"] or {}).get("label"), weeks=r["survived_weeks"])
                          for r in rows],
                    suggested_rates=suggested_rates(rows, recipe, args.target))
    if args.command == "estimate":
        from .polis.service import estimate

        return estimate(load_recipe(recipes, args.recipe), args.seconds_per_decision)
    if args.command == "replay":
        from .persistence.json_files import read_json
        from .polis.report import build_report
        from .polis.runner import replay_seed

        run = read_json(Path(args.run) / "run.json")
        world = replay_seed(run["recipe"], args.seed, Path(args.run) / f"seed-{args.seed}")
        report = build_report(world)
        return dict(weeks_replayed=len(world.decisions), score=report["score"]["total"],
                    collapsed=report["score"]["collapsed"], providers_called=0)
    if args.command == "export":
        from .polis.explorer import export_run_folder
        from .polis.service import SocietyService

        service = SocietyService(args.runs_dir, recipes)
        written = []
        for row in service.runs():
            if row["status"] != "completed" or (args.run and row["id"] != args.run):
                continue
            run = service.read(row["id"])
            written.append(str(export_run_folder(service.folder(row["id"]), args.explorer_dir,
                                                 service.references(run["recipe"]))))
        return dict(exported=written)
    if args.command == "demo":
        from .polis.explorer import export_references

        return dict(exported=[str(p) for p in export_references(load_recipe(recipes, args.recipe),
                                                                 args.explorer_dir)])
    if args.command == "doctor":
        import platform
        from importlib.metadata import version

        from .core.ids import digest
        from .models.config import validate_model
        from .models.health import check_model
        from .polis.runner import engine_fingerprint

        config = json.loads(Path(args.model).read_text(encoding="utf8"))
        validate_model(config)
        result = dict(model_configuration_sha256=digest(config), engine=engine_fingerprint(),
                      python=platform.python_version(), platform=platform.platform(),
                      lmstudio_sdk=version("lmstudio"), model_sha256_declared=config.get("model_sha256"),
                      health=asyncio.run(check_model(config)))
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf8") as stream:
            json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
        return result
    from .models.config import is_model, validate_model
    from .polis.service import SocietyService

    candidate = json.loads(Path(args.model).read_text(encoding="utf8"))
    validate_model(candidate)
    if not is_model(candidate):
        raise ValueError("--model requires a candidate model JSON object")
    recipe = load_recipe(recipes, args.recipe)

    async def play():
        service = SocietyService(args.runs_dir, recipes, explorer=args.explorer_dir)
        run = await service.start(candidate, Path(args.model).stem, recipe, args.parallelism)
        try:
            await service.active[run["id"]][0]
        finally:
            await service.shutdown()
        return service.summary(service.read(run["id"]))

    return asyncio.run(play())


def cli(argv=None):
    # Results are printed with ensure_ascii=False: without this, a redirected stdout on Windows
    # uses the locale code page (cp1252) and fails on characters such as "≈".
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf8")
    root = parser()
    args = root.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "serve":
            serve(args)
            return 0
        result = execute(args)
    except (ValueError, OSError, TypeError, KeyError) as error:
        root.error(str(error))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.command == "doctor":
        return 0 if result["health"]["ok"] else 1
    if args.command == "run":
        return 1 if result.get("status") in {"failed"} else 0
    return 0


if __name__ == "__main__":
    sys.exit(cli())
