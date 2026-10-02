"""Freeze runtime and dependency inputs; refuse resume under changed code."""

import sys
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from ..core.ids import digest


@lru_cache(maxsize=1)
def runtime_fingerprint():
    root = Path(__file__).resolve().parents[1]
    folders = [
        "actions",
        "core",
        "evaluation",
        "memory",
        "persistence",
        "policies",
        "scenarios",
        "world",
    ]
    paths = [p for folder in folders for p in (root / folder).rglob("*.py")]
    paths += [
        root / "benchmark" / name
        for name in [
            "runner.py",
            "service.py",
            "duration.py",
            "parallel.py",
            "scheduler.py",
            "manifests.py",
            "validation.py",
            "recipes.py",
            "fingerprints.py",
            "feasibility.py",
        ]
    ]
    files = {str(p.relative_to(root)): p.read_text() for p in sorted(paths)}
    files["decision.schema.json"] = (root / "actions/decision.schema.json").read_text()
    files["dependencies"] = {
        package: version(package) for package in ["jsonschema", "pydantic", "httpx", "fastapi", "lmstudio"]
    }
    files["python"] = list(sys.version_info[:3])
    return digest(files)
