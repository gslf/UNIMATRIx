"""Start an OpenAI-compatible inference server backed by llama.cpp.

Usage — by catalog key (after `python download.py <key>`):
    python serve.py qwen2.5-14b
    python serve.py qwen2.5-14b --port 8000
    python serve.py qwen2.5-14b --n-gpu-layers 0     # force CPU
    python serve.py qwen2.5-14b --n-ctx 4096

Usage — by direct path to a local GGUF (anywhere on disk):
    python serve.py models/gpt-oss-120b-MXFP4-00001-of-00002.gguf
    python serve.py "D:/llms/gpt-oss-120b-Q4_K_M-00001-of-00002.gguf" \
        --n-gpu-layers 30 --n-ctx 4096 --chat-format chatml

Multi-part GGUFs (`*-00001-of-N.gguf`, `*-00002-of-N.gguf`, ...) are
auto-stitched by llama.cpp. Pass the **first** part — the loader will pick up
the remaining parts from the same directory.

The server exposes /v1/chat/completions on the chosen port; point Unimatrix at
it with `--backend llama_cpp --endpoint http://127.0.0.1:8000`.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from rich.console import Console

from _catalog import get


_CUDA_DLL_PREFIXES = ("cudart64_", "cublas64_", "cublasLt64_")


def _diagnose_cuda_dlls(console: Console, search_dirs: list[Path]) -> None:
    """When the llama_cpp import fails, tell the user which CUDA DLLs are
    actually present in each searched directory. This usually pinpoints
    whether the toolkit is missing or the wrong major version."""
    if not search_dirs:
        console.print(
            "[yellow]no CUDA bin directories were searched.[/] You probably "
            "have only the NVIDIA driver, not the CUDA Toolkit. Install the "
            "Toolkit at the version your wheel requires (typically 12.x for "
            "abetlen wheels, 13.0 for the dougeeai Blackwell wheel)."
        )
        return
    found_anywhere = False
    for d in search_dirs:
        names = []
        try:
            for f in d.iterdir():
                low = f.name.lower()
                if any(low.startswith(p.lower()) for p in _CUDA_DLL_PREFIXES):
                    names.append(f.name)
        except OSError:
            continue
        if names:
            found_anywhere = True
            console.print(f"  [green]found in[/] {d}: {', '.join(sorted(names))}")
        else:
            console.print(f"  [dim]nothing CUDA-related in[/] {d}")
    if not found_anywhere:
        console.print(
            "\n[red]None of the searched directories contains cudart/cublas DLLs.[/]\n"
            "  Install the CUDA Toolkit (not just the driver) — download from\n"
            "  https://developer.nvidia.com/cuda-downloads\n"
            "  Then restart your shell so PATH picks it up, or set\n"
            "  [bold]UNIMATRIX_CUDA_BIN[/]=\"C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v13.0\\bin\""
        )
    else:
        # check version mismatch
        versions = set()
        for d in search_dirs:
            try:
                for f in d.iterdir():
                    m = re.match(r"cudart64_(\d+)\.dll", f.name, re.I)
                    if m:
                        versions.add(m.group(1))
            except OSError:
                pass
        if versions:
            console.print(
                f"\n[yellow]CUDA runtime versions detected on disk:[/] "
                f"{', '.join(sorted(versions))}\n"
                "  If your wheel was built for a different major version, the "
                "load will still fail.\n"
                "  Re-install a wheel matching one of the toolkits above, OR "
                "install a toolkit that matches your wheel."
            )


def _register_cuda_dll_search_paths(console: Console) -> list[Path]:
    """Make sure llama_cpp can load the CUDA runtime DLLs on Windows.

    Pip-installed CUDA wheels for llama-cpp-python depend on cudart, cublas,
    cublasLt — these live in the CUDA Toolkit's `bin` folder. If the user
    didn't add it to PATH, the import below explodes with a misleading
    "Could not find module llama.dll" error. Use os.add_dll_directory()
    (Python 3.8+ on Windows) so the import succeeds even without PATH edits.
    """
    if sys.platform != "win32":
        return []
    candidates: list[Path] = []
    # 1) explicit override
    env = os.environ.get("UNIMATRIX_CUDA_BIN")
    if env:
        candidates.append(Path(env))
    # 2) CUDA_PATH set by the toolkit installer
    for var in ("CUDA_PATH", "CUDA_HOME"):
        v = os.environ.get(var)
        if v:
            candidates.append(Path(v) / "bin")
    # 3) versioned env vars set by the installer (CUDA_PATH_V12_8 etc)
    for k, v in os.environ.items():
        if k.startswith("CUDA_PATH_V") and v:
            candidates.append(Path(v) / "bin")
    # 4) standard install prefix on Windows
    base = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if base.exists():
        for d in sorted(base.glob("v*")):
            candidates.append(d / "bin")
    # 5) PATH entries that mention CUDA
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if p and "cuda" in p.lower():
            candidates.append(Path(p))

    added: list[Path] = []
    seen: set[Path] = set()
    for c in candidates:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c in seen or not c.is_dir():
            continue
        seen.add(c)
        try:
            os.add_dll_directory(str(c))
            added.append(c)
        except (OSError, FileNotFoundError):
            pass
    if added:
        console.print(
            "[dim]registered CUDA DLL search paths:[/]"
            + "".join(f"\n  - {p}" for p in added)
        )
    else:
        console.print(
            "[yellow]no CUDA bin directory found.[/] If the next import "
            "fails with 'Could not find module llama.dll', it actually means "
            "a CUDA dependency is missing.\n"
            "  Install the CUDA Toolkit (not just the driver) at the version "
            "matching your llama-cpp-python wheel,\n"
            "  or set [bold]UNIMATRIX_CUDA_BIN[/] to its `bin` folder."
        )
    return added


_PART_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)


def _resolve_model(arg: str, console: Console) -> tuple[Path, str, int, str]:
    """Return (path, alias, default_ctx, chat_format) for either a catalog key
    or a direct .gguf path. Validates existence and warns on multi-part files
    that don't point at part 1."""
    if arg.lower().endswith(".gguf") or "/" in arg or "\\" in arg:
        path = Path(arg).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"file not found: {path}")
        m = _PART_RE.search(path.name)
        if m and m.group(1) != "00001":
            console.print(
                f"[yellow]warning[/]: {path.name} is part {m.group(1)} of "
                f"{m.group(2)}. Always pass the first part — llama.cpp loads "
                "the rest automatically from the same folder."
            )
            # rewrite to part 1
            first = path.with_name(_PART_RE.sub(f"-00001-of-{m.group(2)}.gguf", path.name))
            if first.exists():
                console.print(f"[yellow]auto-corrected to[/]: {first}")
                path = first
        # Verify the sibling parts are present so we fail fast.
        m = _PART_RE.search(path.name)
        if m:
            total = int(m.group(2))
            for i in range(1, total + 1):
                sibling = path.with_name(
                    _PART_RE.sub(f"-{i:05d}-of-{total:05d}.gguf", path.name)
                )
                if not sibling.exists():
                    raise SystemExit(
                        f"missing GGUF part {i}/{total}: expected {sibling}"
                    )
        alias = path.stem
        return path, alias, 8192, "chatml"

    # treat as catalog key
    entry = get(arg)
    if not entry.local_path.exists():
        raise SystemExit(
            f"model file not found: {entry.local_path}\n"
            f"run: python download.py {entry.key}"
        )
    return entry.local_path, entry.key, entry.context, entry.chat_format


def main() -> int:
    console = Console()
    parser = argparse.ArgumentParser(
        description="Run llama.cpp as an OpenAI-compatible server"
    )
    parser.add_argument(
        "model",
        help="catalog key from models.toml OR path to a .gguf file "
             "(for multi-part files, point at part 00001-of-N)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--n-gpu-layers", type=int, default=-1,
        help="how many layers to offload to GPU (-1 = all, 0 = CPU only). "
             "For models that exceed VRAM, set a finite count and llama.cpp "
             "will keep the rest on CPU.",
    )
    parser.add_argument("--n-ctx", type=int, default=None,
                        help="context window (catalog default if omitted)")
    parser.add_argument("--n-threads", type=int, default=None,
                        help="CPU threads (default: auto)")
    parser.add_argument("--n-threads-batch", type=int, default=None,
                        help="CPU threads for batch processing (default: auto)")
    parser.add_argument("--n-batch", type=int, default=512)
    parser.add_argument(
        "--chat-format", default=None,
        help="override chat template (chatml | mistral-instruct | llama-3 | ...)",
    )
    parser.add_argument(
        "--alias", default=None,
        help="model alias served at /v1/models (defaults to filename or catalog key)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--reasoning", default=None, choices=("low", "medium", "high"),
        help="set reasoning effort for the served model. The middleware "
             "translates this to the model family's native dialect (harmony "
             "system marker, Qwen3 /no_think, ...). See --reasoning-style.",
    )
    parser.add_argument(
        "--reasoning-style", default=None,
        choices=("harmony", "qwen3", "deepseek-r1", "generic"),
        help="override the reasoning dialect (default: auto-detect from model "
             "name). 'generic' is a no-op and just warns at startup.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="forward llama.cpp's verbose logs to stdout",
    )
    args = parser.parse_args()

    path, alias, default_ctx, default_chat = _resolve_model(args.model, console)
    n_ctx = args.n_ctx if args.n_ctx is not None else default_ctx
    chat_format = args.chat_format or default_chat
    if args.alias:
        alias = args.alias

    style = args.reasoning_style or _detect_reasoning_style(alias, path)
    if args.reasoning and style == "generic":
        console.print(
            "[yellow]warning[/]: --reasoning was set but the model didn't "
            "match any known reasoning dialect (gpt-oss/harmony, qwen3, "
            "deepseek-r1). The flag will be a no-op. Override with "
            "--reasoning-style if auto-detection got it wrong."
        )

    console.print(
        f"[bold]starting llama.cpp server[/]\n"
        f"  model      : {path}\n"
        f"  alias      : {alias}\n"
        f"  chat fmt   : {chat_format}\n"
        f"  n_ctx      : {n_ctx}\n"
        f"  gpu layers : {args.n_gpu_layers}  (-1 = all)\n"
        f"  reasoning  : {args.reasoning or '(not injected)'} "
        f"(style: {style})\n"
        f"  endpoint   : http://{args.host}:{args.port}/v1/chat/completions"
    )

    # Make CUDA DLLs discoverable BEFORE we try to import the C++ extension.
    cuda_dirs = _register_cuda_dll_search_paths(console)

    # Import lazily so `--help` works without llama-cpp-python installed.
    try:
        import llama_cpp
        from llama_cpp.server.app import create_app
        from llama_cpp.server.settings import ModelSettings, ServerSettings
    except ImportError as exc:
        msg = str(exc).lower()
        console.print(f"[red]ImportError:[/] {exc}")
        console.print(
            f"  python executable : [bold]{sys.executable}[/]\n"
            f"  python version    : {sys.version.split()[0]}\n"
            f"  sys.prefix        : {sys.prefix}"
        )
        # 1) native lib failed to load (DLL deps missing)
        if any(t in msg for t in (
            "dll load failed", "shared library", "_ctypes_extensions",
            "llama.dll",
        )):
            console.print(
                "\n[yellow]llama-cpp-python is installed but its native "
                "library failed to load.[/]"
            )
            _diagnose_cuda_dlls(console, cuda_dirs)
        # 2) the server extras (FastAPI / uvicorn / sse-starlette) are missing
        elif any(t in msg for t in (
            "fastapi", "uvicorn", "sse_starlette", "sse-starlette",
            "pydantic_settings", "starlette",
        )):
            console.print(
                "\n[yellow]llama-cpp-python is installed but the SERVER "
                "extras (FastAPI / uvicorn / sse-starlette / pydantic-settings) "
                "are missing.[/]\n"
                "This happens when the wheel was installed directly via URL "
                "— pip skips extras in that case.\n"
                f"Fix: [bold]\"{sys.executable}\" -m pip install .[/]"
            )
        # 3) the package itself isn't importable
        else:
            console.print(
                "\n[yellow]llama-cpp-python doesn't appear to be installed in "
                "this venv.[/]\n"
                "Verify with:\n"
                f"  [bold]\"{sys.executable}\" -m pip show llama-cpp-python[/]\n"
                "If that says 'not found', either:\n"
                "  • you installed into a different venv (re-activate the "
                "right one), or\n"
                "  • the install failed silently (re-run it and watch the "
                "output for errors)."
            )
        return 1
    except (OSError, RuntimeError) as exc:
        # Misleading on Windows: "Could not find module llama.dll" almost
        # always means one of llama.dll's CUDA dependencies is missing.
        console.print(f"[red]llama_cpp failed to load its native library:[/] {exc}\n")
        _diagnose_cuda_dlls(console, cuda_dirs)
        return 1

    # Detect whether the installed wheel was built with GPU support.
    gpu_offload_supported = False
    try:
        gpu_offload_supported = bool(llama_cpp.llama_supports_gpu_offload())
    except Exception:
        # Older versions of llama-cpp-python don't expose this; treat as unknown.
        gpu_offload_supported = None  # type: ignore[assignment]

    if gpu_offload_supported is False:
        console.print(
            "[red]WARNING: the installed llama-cpp-python was built WITHOUT "
            "GPU support — everything will run on CPU.[/]\n"
            "  This usually means [bold]pip install llama-cpp-python[server][/] "
            "fetched the CPU wheel from PyPI (PyPI does not host CUDA wheels).\n\n"
            "  See [bold]inference_server/README.md → section 1b[/] for the "
            "correct install command. Quick reference:\n"
            "    • CUDA 12.1–12.5: use abetlen's index, e.g.\n"
            "      [bold]pip install --upgrade --force-reinstall "
            "llama-cpp-python[server] \\\n"
            "        --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu125[/]\n"
            "    • RTX 5090 / Blackwell (sm_120): no abetlen wheel exists. Use\n"
            "      the community wheel from\n"
            "      https://github.com/dougeeai/llama-cpp-python-wheels/releases\n"
            "      OR build from source with\n"
            "      [bold]CMAKE_ARGS=\"-DGGML_CUDA=on -DGGML_CUDA_FORCE_CUBLAS=1 "
            "-DCMAKE_CUDA_ARCHITECTURES=120\" pip install --no-binary "
            "llama-cpp-python --force-reinstall \"llama-cpp-python[server]\"[/]\n"
            "  Then re-run this command."
        )
    elif gpu_offload_supported is True:
        console.print("[green]GPU offload is supported by the installed build.[/]")
        if args.n_gpu_layers == 0:
            console.print(
                "[yellow]note[/]: --n-gpu-layers=0 → CPU-only despite GPU "
                "build. Use -1 for full offload."
            )
    else:
        console.print(
            "[yellow]could not detect GPU offload support[/] "
            "(older llama-cpp-python). Watch the load logs below."
        )

    server_settings = ServerSettings(host=args.host, port=args.port)

    # Build kwargs and only include optional fields when the user set them.
    # ModelSettings is strict about unknown fields and rejects None for ints
    # that are typed as required, so we omit anything we don't have a value for
    # and rely on the library's defaults.
    model_kwargs: dict = {
        "model": str(path),
        "model_alias": alias,
        "chat_format": chat_format,
        "n_ctx": n_ctx,
        "n_gpu_layers": args.n_gpu_layers,
        "n_batch": args.n_batch,
        "seed": args.seed,
        "verbose": args.verbose,
    }
    if args.n_threads is not None:
        model_kwargs["n_threads"] = args.n_threads
    if args.n_threads_batch is not None:
        model_kwargs["n_threads_batch"] = args.n_threads_batch

    # Filter out fields not supported by the installed llama-cpp-python.
    valid_fields = set(ModelSettings.model_fields.keys())
    dropped = [k for k in model_kwargs if k not in valid_fields]
    for k in dropped:
        console.print(f"[yellow]ignoring unsupported ModelSettings field[/]: {k}")
        model_kwargs.pop(k)

    model_settings = [ModelSettings(**model_kwargs)]
    app = create_app(server_settings=server_settings, model_settings=model_settings)

    if args.reasoning and style != "generic":
        app = _ReasoningInjector(app, args.reasoning, style)
        console.print(
            f"[green]reasoning injection active[/]: dialect={style}, "
            f"level={args.reasoning}."
        )

    # Wrap outermost so the monitor records what clients actually sent
    # (before any server-side rewrite by the reasoning injector).
    from monitor import ChatMonitor
    app = ChatMonitor(app)
    console.print(
        f"[green]chat monitor[/]: http://{args.host}:{args.port}/monitor"
    )

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _detect_reasoning_style(alias: str, path: Path) -> str:
    """Guess the reasoning dialect from the model alias or filename.

    Returns 'harmony', 'qwen3', 'deepseek-r1', or 'generic'."""
    s = f"{alias} {path.name}".lower()
    if "gpt-oss" in s or "harmony" in s:
        return "harmony"
    if "qwen3" in s:
        return "qwen3"
    if "deepseek" in s and "r1" in s:
        return "deepseek-r1"
    return "generic"


class _ReasoningInjector:
    """ASGI middleware that rewrites POST /v1/chat/completions bodies to apply a
    reasoning-effort hint in the model family's native dialect.

      harmony / gpt-oss : prepend 'Reasoning: <level>' to the system message.
      qwen3             : append '/no_think' to the last user message when
                          level == 'low'; medium/high leaves the default
                          (thinking on) since Qwen3 is binary, not tiered.
      deepseek-r1       : R1 doesn't expose effort levels — no-op + a no-op
                          dialect is logged at startup. Kept here for symmetry.
      generic           : not registered (caller skips construction).
    """

    def __init__(self, app, level: str, style: str) -> None:
        self.app = app
        self.level = level
        self.style = style

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").endswith("/chat/completions")
        ):
            await self.app(scope, receive, send)
            return

        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)

        try:
            import json as _json
            data = _json.loads(body or b"{}")
        except (ValueError, TypeError):
            data = None

        if isinstance(data, dict) and isinstance(data.get("messages"), list):
            if self.style == "harmony":
                self._apply_harmony(data["messages"])
            elif self.style == "qwen3":
                self._apply_qwen3(data["messages"])
            # deepseek-r1: no-op (effort not tunable)
            body = _json.dumps(data).encode("utf-8")

        new_headers = [
            (k, v) for k, v in scope.get("headers", [])
            if k.lower() != b"content-length"
        ]
        new_headers.append((b"content-length", str(len(body)).encode()))
        scope = dict(scope)
        scope["headers"] = new_headers

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            # After our buffered body is consumed, defer to the real receive
            # so the downstream handler can still observe genuine disconnects
            # instead of getting a spurious one from us (which llama-cpp-python
            # logs as "Disconnected from client ... before llm invoked" → 400).
            return await receive()

        await self.app(scope, replay, send)

    def _apply_harmony(self, msgs: list) -> None:
        marker = f"Reasoning: {self.level}"
        if msgs and isinstance(msgs[0], dict) and msgs[0].get("role") == "system":
            content = msgs[0].get("content") or ""
            if isinstance(content, str) and marker not in content:
                msgs[0]["content"] = (marker + "\n" + content).strip()
        else:
            msgs.insert(0, {"role": "system", "content": marker})

    def _apply_qwen3(self, msgs: list) -> None:
        # Qwen3 thinking is binary. Map low→/no_think; medium/high leaves the
        # default behavior (thinking on) untouched.
        if self.level != "low":
            return
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str) and "/no_think" not in content:
                    m["content"] = content.rstrip() + " /no_think"
                return


if __name__ == "__main__":
    sys.exit(main())
