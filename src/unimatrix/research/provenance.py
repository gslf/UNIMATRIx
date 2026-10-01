"""Content identity for standalone offline analyses and their installed inputs."""

import hashlib
import sys
from importlib.metadata import version
from pathlib import Path

from ..core.ids import digest

DEPENDENCIES = ("jsonschema", "pydantic", "httpx", "fastapi", "lmstudio")


def source_manifest(root):
    """Bind package code and JSON catalogs; exclude paths, caches and local evidence."""
    root = Path(root)
    paths = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in {".py", ".json"})
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def analysis_provenance(entry_point):
    """Identify analysis code separately from the evaluated run's frozen runtime."""
    identity = dict(format="unimatrix.analysis-provenance.v1", entry_point=entry_point,
                    python=list(sys.version_info[:3]),
                    dependencies={name: version(name) for name in DEPENDENCIES},
                    files=source_manifest(Path(__file__).resolve().parents[1]))
    return dict(identity, sha256=digest(identity))
