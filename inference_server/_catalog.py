"""Tiny TOML loader shared by download.py and serve.py.

Uses the stdlib `tomllib` (Python 3.11+).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


HERE = Path(__file__).resolve().parent
CATALOG_PATH = HERE / "models.toml"
MODELS_DIR = HERE / "models"


@dataclass
class ModelEntry:
    key: str
    repo: str
    file: str
    context: int
    chat_format: str

    @property
    def local_path(self) -> Path:
        return MODELS_DIR / self.file


def load_catalog() -> dict[str, ModelEntry]:
    raw = tomllib.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    out: dict[str, ModelEntry] = {}
    for k, v in raw.get("models", {}).items():
        out[k] = ModelEntry(
            key=k,
            repo=v["repo"],
            file=v["file"],
            context=int(v.get("context", 8192)),
            chat_format=v.get("chat_format", "chatml"),
        )
    return out


def get(key: str) -> ModelEntry:
    cat = load_catalog()
    if key not in cat:
        keys = ", ".join(sorted(cat))
        raise SystemExit(f"unknown model '{key}'. available: {keys}")
    return cat[key]
