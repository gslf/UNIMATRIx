"""Atomic local JSON persistence, including private credential files."""

import json
import os
import tempfile
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def write_json(path, data, *, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if private and os.name == "nt":
        from .windows_private import create_temporary

        fd, temporary = create_temporary(path.parent)
    else:
        # mkstemp creates owner-only files on POSIX, regardless of the process umask.
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".write-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
