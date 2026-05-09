from __future__ import annotations

import argparse
import asyncio
import importlib.resources as ir
import signal
import sys
from pathlib import Path

import uvicorn


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


from .log_console import LoggingConsole
from .persistence import Registry
from .session import SessionManager
from .web import build_app


async def _run(args: argparse.Namespace) -> int:
    console = LoggingConsole()

    runs_dir = Path(args.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    configs_dir = Path(args.configs_dir)

    registry = Registry(runs_dir)
    session = SessionManager(
        configs_dir=configs_dir,
        runs_dir=runs_dir,
        console=console,
        registry=registry,
        defaults={
            "backend": args.backend,
            "endpoint": args.endpoint,
            "model": args.model,
        },
    )

    static_dir = Path(ir.files("unimatrix.web") / "static")  # type: ignore[arg-type]
    app = build_app(session, registry, runs_dir, static_dir)

    config = uvicorn.Config(
        app, host=args.host, port=args.port, log_level=args.log_level, lifespan="off"
    )
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        console.log("[bold red]shutdown signal received[/]")
        server.should_exit = True

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

    console.log(
        f"[bold green]Unimatrix online[/] — UI at http://{args.host}:{args.port}/"
    )
    console.log(
        f"[dim]No simulation running. Pick a config in {configs_dir}/ "
        "and click Start in the control panel.[/]"
    )
    if not configs_dir.exists():
        console.log(
            f"[yellow]warning[/]: configs dir {configs_dir} does not exist; "
            "create it and drop JSON configs in to use the Start button."
        )

    try:
        await server.serve()
    except KeyboardInterrupt:
        _signal_handler()
    finally:
        if session.is_active:
            console.log("[bold]server stopping — winding down active simulation…[/]")
            try:
                await session.stop()
            except Exception as exc:
                console.log(f"[red]error during simulation shutdown[/]: {exc!r}")
        console.log("[bold]bye[/]")
    return 0


def cli() -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(prog="unimatrix")
    parser.add_argument(
        "--configs-dir", default="config",
        help="directory holding *.json simulation configs (default: ./config)",
    )
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--backend", choices=("vllm", "llama_cpp", "stub"), default=None,
        help="override config inference.backend at start time",
    )
    parser.add_argument("--endpoint", default=None,
                        help="override config inference.endpoint at start time")
    parser.add_argument("--model", default=None,
                        help="override config inference.model at start time")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(cli())
