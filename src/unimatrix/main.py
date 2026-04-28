"""CLI entry point.

Wires together: config load -> registry record -> run store -> memory manager ->
inference client -> orchestrator -> web server. Runs the orchestrator and the
HTTP server concurrently; either Ctrl-C or a SIGTERM cleanly shuts both down.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.resources as ir
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from rich.console import Console


def _force_utf8_stdio() -> None:
    """On Windows, stdout often defaults to cp1252 and rich emits unicode.
    Reconfigure to utf-8 with replacement so logging never crashes the loop."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from .config import Config, load_config
from .inference import InferenceClient
from .memory import MemoryManager
from .orchestrator import Orchestrator
from .persistence import Registry, RunStore
from .web import build_app


def _ts_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def _run(args: argparse.Namespace) -> int:
    console = Console()
    cfg_path = Path(args.config)
    cfg: Config = load_config(cfg_path)

    if args.backend:
        cfg.inference.backend = args.backend  # type: ignore[assignment]
    if args.endpoint:
        cfg.inference.endpoint = args.endpoint
    if args.model:
        cfg.inference.model = args.model

    # Sanity check: warn loudly if the user is running a heavy backend with
    # settings that almost certainly produce timeouts.
    if cfg.inference.backend != "stub":
        if cfg.inference.max_batch_size > 16:
            console.log(
                f"[yellow]warning[/]: inference.max_batch_size = "
                f"{cfg.inference.max_batch_size}. llama.cpp serves requests "
                "single-slot by default, so a high concurrency just queues "
                "them up and triggers timeouts. Recommended: 4-8 for "
                "llama.cpp, 32-64 for vLLM."
            )
        if cfg.inference.request_timeout_seconds < 120:
            console.log(
                f"[yellow]warning[/]: inference.request_timeout_seconds = "
                f"{cfg.inference.request_timeout_seconds}s is very short for "
                "a real LLM backend. Bump to 300-600s for big models on "
                "partial-GPU offload."
            )
        if (cfg.social.max_idle_decisions_per_tick == 0
                and len(cfg.agents) > 12):
            console.log(
                f"[yellow]warning[/]: social.max_idle_decisions_per_tick = 0 "
                f"(unlimited) with {len(cfg.agents)} agents. Each tick will "
                "fire that many decision requests. Recommended for slow "
                "backends: set to 4-8."
            )

    runs_dir = Path(args.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_slug = f"{cfg.simulation.name}_{_ts_slug()}"
    run_subdir = runs_dir / run_slug
    run_subdir.mkdir(parents=True, exist_ok=True)
    db_path = runs_dir / f"{run_slug}.db"

    registry = Registry(runs_dir)
    run_id = registry.register(
        cfg.simulation.name, str(db_path), json.loads(cfg.model_dump_json())
    )
    console.log(f"[bold]registered run[/] id={run_id} db={db_path}")

    store = RunStore(db_path)
    memory = MemoryManager(
        cfg.memory, store, run_id, persist_dir=str(run_subdir / "chroma")
    )
    inference = InferenceClient(cfg.inference)

    orch = Orchestrator(
        cfg=cfg,
        store=store,
        memory=memory,
        inference=inference,
        registry=registry,
        run_id=run_id,
        run_dir=run_subdir,
        console=console,
    )
    await orch.initialize()

    static_dir = Path(ir.files("unimatrix.web") / "static")  # type: ignore[arg-type]
    app = build_app(orch, registry, runs_dir, static_dir)

    config = uvicorn.Config(
        app, host=args.host, port=args.port, log_level=args.log_level, lifespan="off"
    )
    server = uvicorn.Server(config)

    # Graceful shutdown wiring
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        console.log("[bold red]shutdown signal received[/]")
        orch.request_stop()
        server.should_exit = True
        stop_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows: signal handlers not always installable; rely on KeyboardInterrupt.
                pass

    orch_task = asyncio.create_task(orch.run(), name="orchestrator")
    server_task = asyncio.create_task(server.serve(), name="uvicorn")
    console.log(
        f"[bold green]Unimatrix online[/] — UI at http://{args.host}:{args.port}/"
    )
    try:
        done, pending = await asyncio.wait(
            {orch_task, server_task}, return_when=asyncio.FIRST_EXCEPTION
        )
        for t in done:
            exc = t.exception()
            if exc is None:
                continue
            console.log(f"[red]task '{t.get_name()}' failed[/]: {exc!r}")
            # Walk the cause chain so RuntimeErrors raised "from" httpx errors
            # don't drop the actual underlying HTTP context.
            cause = exc.__cause__
            while cause is not None:
                console.log(f"  [red]caused by[/]: {cause!r}")
                cause = cause.__cause__
            console.print_exception(show_locals=False)
        # Clean shutdown
        orch.request_stop()
        server.should_exit = True
        for t in pending:
            try:
                await asyncio.wait_for(t, timeout=10.0)
            except asyncio.TimeoutError:
                t.cancel()
    except KeyboardInterrupt:
        _signal_handler()
        for t in (orch_task, server_task):
            t.cancel()
    finally:
        await inference.aclose()
        await memory.close()
        store.close()
        console.log("[bold]bye[/]")
    return 0


def cli() -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(prog="unimatrix")
    parser.add_argument("--config", required=True, help="path to JSON config")
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--backend", choices=("vllm", "llama_cpp", "stub"), default=None,
        help="override config inference.backend",
    )
    parser.add_argument("--endpoint", default=None,
                        help="override config inference.endpoint")
    parser.add_argument("--model", default=None,
                        help="override config inference.model")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(cli())
