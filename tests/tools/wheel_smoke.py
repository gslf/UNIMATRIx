"""Exercise an installed release wheel from outside its source checkout."""

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import unimatrix
from unimatrix.web.server import build_app


def main():
    installed = Path(unimatrix.__file__).resolve()
    if not installed.is_relative_to(Path(sys.prefix).resolve()):
        raise AssertionError(f"unimatrix_not_imported_from_wheel:{installed}")

    with TemporaryDirectory(prefix="unimatrix-wheel-smoke-") as temporary:
        root = Path(temporary)
        command = Path(sys.prefix) / "bin" / "unimatrix"
        result = subprocess.run([str(command), "recipes"], cwd=root, capture_output=True, text=True,
                                encoding="utf8", check=True)
        if [row["id"] for row in json.loads(result.stdout)] != ["standard-v2.1", "hell-v1"]:
            raise AssertionError("installed_recipe_catalog_mismatch")
        result = subprocess.run([str(command), "simulate", "--policy", "naive", "--seed", "1"], cwd=root,
                                capture_output=True, text=True, encoding="utf8", check=True)
        if "total" not in json.loads(result.stdout)["score"]:
            raise AssertionError("installed_simulation_failed")

        app = build_app(runs_dir=root / "runs", models_dir=root / "models", societies_dir=root / "societies")
        with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
            for path in ("/", "/recipe-lab", "/society/runs/x", "/static/home.js", "/static/society-lab.js",
                         "/static/society-report.js", "/static/society-village.js", "/static/security.js"):
                response = client.get(path)
                if response.status_code != 200 or not response.content:
                    raise AssertionError(f"installed_dashboard_route_failed:{path}:{response.status_code}")
            response = client.get("/api/polis/recipes")
            if response.status_code != 200 or [row["id"] for row in response.json()] != ["standard-v2.1", "hell-v1"]:
                raise AssertionError("installed_recipe_api_failed")
            if client.get("/api/personalities").status_code != 200:
                raise AssertionError("installed_personalities_missing")

    print(f"installed_wheel_smoke_passed:{installed}")


if __name__ == "__main__":
    main()
