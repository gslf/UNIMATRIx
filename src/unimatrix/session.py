from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config, load_config
from pydantic import ValidationError
from .inference import InferenceClient
from .log_console import LoggingConsole
from .memory import MemoryManager
from .orchestrator import Orchestrator
from .persistence import Registry, RunStore


def _ts_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class SessionError(RuntimeError):
    """Raised for user-visible session errors (404, 409, etc)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class SessionManager:
    """Owns the one running simulation and its dependencies, if any."""

    def __init__(
        self,
        *,
        configs_dir: Path,
        runs_dir: Path,
        console: LoggingConsole,
        registry: Registry,
        defaults: dict[str, Any] | None = None,
    ) -> None:
        self.configs_dir = Path(configs_dir)
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.console = console
        self.registry = registry
        self.defaults = defaults or {}
        self._orch: Orchestrator | None = None
        self._task: asyncio.Task[None] | None = None
        self._store: RunStore | None = None
        self._memory: MemoryManager | None = None
        self._inference: InferenceClient | None = None
        # asyncio.Lock created lazily so the manager can be constructed
        # outside an event loop.
        self._lock: asyncio.Lock | None = None

    # ----- introspection -----

    @property
    def orchestrator(self) -> Orchestrator | None:
        return self._orch

    @property
    def is_active(self) -> bool:
        return (
            self._orch is not None
            and self._task is not None
            and not self._task.done()
        )

    def list_configs(self) -> list[dict]:
        """Available configs as `{file, simulation_name, agents}`."""
        out: list[dict] = []
        if not self.configs_dir.exists():
            return out
        for p in sorted(self.configs_dir.glob("*.json")):
            entry: dict[str, Any] = {"file": p.name}
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                sim = raw.get("simulation") or {}
                entry["simulation_name"] = sim.get("name") or p.stem
                entry["agents"] = len(raw.get("agents") or [])
            except Exception as exc:
                entry["simulation_name"] = p.stem
                entry["error"] = f"failed to parse: {exc}"
            out.append(entry)
        return out

    # ----- lifecycle -----

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _apply_inference_overrides(self, cfg: Config) -> None:
        if self.defaults.get("backend"):
            cfg.inference.backend = self.defaults["backend"]
        if self.defaults.get("endpoint"):
            cfg.inference.endpoint = self.defaults["endpoint"]
        if self.defaults.get("model"):
            cfg.inference.model = self.defaults["model"]

    async def _bring_up(
        self,
        cfg: Config,
        run_id: int,
        run_subdir: Path,
        *,
        resume: bool,
    ) -> Orchestrator:
        store = RunStore(self.runs_dir / f"{run_subdir.name}.db")
        memory = MemoryManager(
            cfg.memory, store, run_id, persist_dir=str(run_subdir / "chroma")
        )
        inference = InferenceClient(cfg.inference, console=self.console)
        orch = Orchestrator(
            cfg=cfg,
            store=store,
            memory=memory,
            inference=inference,
            registry=self.registry,
            run_id=run_id,
            run_dir=run_subdir,
            console=self.console,
        )
        try:
            if resume:
                await orch.initialize_from_existing()
            else:
                await orch.initialize()
        except Exception as exc:
            self.console.log(f"[red]initialization failed[/]: {exc!r}")
            try:
                await inference.aclose()
            except Exception:
                pass
            try:
                await memory.close()
            except Exception:
                pass
            try:
                store.close()
            except Exception:
                pass
            from .persistence import utc_now_iso
            self.registry.update_status(run_id, "failed", utc_now_iso())
            raise SessionError(500, f"initialization failed: {exc}") from exc

        self._orch = orch
        self._store = store
        self._memory = memory
        self._inference = inference
        self._task = asyncio.create_task(
            self._run_with_cleanup(), name=f"orchestrator-{run_id}"
        )
        return orch

    async def start(self, config_file: str) -> int:
        """Spin up a new simulation from `<configs_dir>/<config_file>`."""
        async with self._get_lock():
            if self.is_active:
                raise SessionError(409, "a simulation is already running")
            cfg_path = self.configs_dir / config_file
            if not cfg_path.is_file():
                raise SessionError(
                    404, f"config '{config_file}' not found in {self.configs_dir}"
                )
            try:
                cfg: Config = load_config(cfg_path)
            except Exception as exc:
                raise SessionError(400, f"invalid config: {exc}") from exc

            self._apply_inference_overrides(cfg)

            run_slug = f"{cfg.simulation.name}_{_ts_slug()}"
            run_subdir = self.runs_dir / run_slug
            run_subdir.mkdir(parents=True, exist_ok=True)
            db_path = self.runs_dir / f"{run_slug}.db"

            run_id = self.registry.register(
                cfg.simulation.name,
                str(db_path),
                json.loads(cfg.model_dump_json()),
            )
            self.console.log(
                f"[bold]registered run[/] id={run_id} db={db_path} "
                f"(config: {config_file})"
            )
            await self._bring_up(cfg, run_id, run_subdir, resume=False)
            return run_id

    async def resume(self, run_id: int) -> None:
        """Re-attach to a previously-paused run and resume ticking.

        Only runs whose registry status is exactly `paused` are eligible.
        """
        async with self._get_lock():
            if self.is_active:
                raise SessionError(409, "a simulation is already running")
            info = self.registry.get(run_id)
            if info is None:
                raise SessionError(404, f"run {run_id} not found")
            if (info.get("status") or "") != "paused":
                raise SessionError(
                    409,
                    f"run {run_id} is '{info.get('status')}', only paused runs "
                    "can be resumed",
                )
            try:
                cfg_dict = json.loads(info["config_json"])
                cfg: Config = Config.model_validate(cfg_dict)
            except (ValidationError, ValueError) as exc:
                raise SessionError(
                    500, f"saved config for run {run_id} is invalid: {exc}"
                ) from exc

            self._apply_inference_overrides(cfg)

            db_path = Path(info["db_path"])
            if not db_path.exists():
                raise SessionError(
                    410, f"run {run_id} db file missing at {db_path}"
                )
            run_subdir = self.runs_dir / db_path.stem
            run_subdir.mkdir(parents=True, exist_ok=True)

            self.console.log(
                f"[bold]resuming run[/] id={run_id} db={db_path}"
            )
            await self._bring_up(cfg, run_id, run_subdir, resume=True)
            # Loop will tick immediately; flip status from "paused" to running.
            self.registry.update_status(run_id, "running")

    async def _run_with_cleanup(self) -> None:
        orch = self._orch
        if orch is None:
            return
        try:
            await orch.run()
        except asyncio.CancelledError:
            self.console.log("[yellow]orchestrator cancelled[/]")
            raise
        except Exception as exc:
            self.console.log(f"[red]orchestrator crashed[/]: {exc!r}")
        finally:
            await self._teardown()

    async def stop(self) -> bool:
        """Stop the running simulation. Returns False if nothing to stop."""
        async with self._get_lock():
            if not self.is_active:
                return False
            assert self._orch is not None and self._task is not None
            orch = self._orch
            task = self._task
            self.console.log("[bold red]stopping simulation[/]")
            orch.request_stop()
        # Released the lock before waiting so /state etc. stay responsive.
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=20.0)
        except asyncio.TimeoutError:
            self.console.log(
                "[yellow]orchestrator did not stop within 20s — cancelling[/]"
            )
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return True

    async def _teardown(self) -> None:
        if self._inference is not None:
            try:
                await self._inference.aclose()
            except Exception as exc:
                self.console.log(f"[yellow]inference close error[/]: {exc!r}")
        if self._memory is not None:
            try:
                await self._memory.close()
            except Exception as exc:
                self.console.log(f"[yellow]memory close error[/]: {exc!r}")
        if self._store is not None:
            try:
                self._store.close()
            except Exception as exc:
                self.console.log(f"[yellow]store close error[/]: {exc!r}")
        self._orch = None
        self._store = None
        self._memory = None
        self._inference = None
        self._task = None
        self.console.log("[dim]simulation torn down[/]")
