"""Download a GGUF model from HuggingFace into ./models/.

Usage:
    python download.py qwen2.5-14b
    python download.py mistral-small-24b
    python download.py qwen2.5-3b              # small CPU-friendly fallback

Resumes on partial downloads; skips if the file already exists.
"""
from __future__ import annotations

import argparse
import sys

from huggingface_hub import hf_hub_download
from rich.console import Console

from _catalog import MODELS_DIR, get, load_catalog


def main() -> int:
    console = Console()
    parser = argparse.ArgumentParser(description="Download a GGUF model")
    parser.add_argument("model", nargs="?", help="key from models.toml")
    parser.add_argument("--list", action="store_true", help="list available models")
    args = parser.parse_args()

    if args.list or not args.model:
        cat = load_catalog()
        console.print("[bold]Available models:[/]")
        for k, m in cat.items():
            present = "[green]downloaded[/]" if m.local_path.exists() else "[dim]not downloaded[/]"
            console.print(f"  {k:24} {m.repo}/{m.file}  {present}")
        return 0

    entry = get(args.model)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if entry.local_path.exists():
        console.print(f"[green]already present[/]: {entry.local_path}")
        return 0
    console.print(f"[bold]downloading[/] {entry.repo} :: {entry.file}")
    path = hf_hub_download(
        repo_id=entry.repo,
        filename=entry.file,
        local_dir=str(MODELS_DIR),
    )
    console.print(f"[green]saved[/]: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
