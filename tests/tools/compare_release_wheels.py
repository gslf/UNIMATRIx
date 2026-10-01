"""Verify that rebuilding the sdist preserves every installed wheel file."""

import argparse
import hashlib
from pathlib import Path
from zipfile import ZipFile


def files(path):
    with ZipFile(path) as archive:
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if not name.endswith("/RECORD")
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direct_wheel", type=Path)
    parser.add_argument("sdist_wheel", type=Path)
    args = parser.parse_args()
    direct, rebuilt = files(args.direct_wheel), files(args.sdist_wheel)
    if direct != rebuilt:
        missing = sorted(direct.keys() - rebuilt.keys())
        added = sorted(rebuilt.keys() - direct.keys())
        changed = sorted(name for name in direct.keys() & rebuilt.keys()
                         if direct[name] != rebuilt[name])
        parser.error(f"sdist_rebuild_mismatch:missing={missing},added={added},changed={changed}")
    print(f"sdist_rebuild_matches_direct_wheel:{len(direct)}_files")


if __name__ == "__main__":
    main()
