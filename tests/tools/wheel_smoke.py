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
        result = subprocess.run(
            [str(command), "recipes"], cwd=root, capture_output=True, text=True, check=True
        )
        recipes = json.loads(result.stdout)
        if {row["id"]: row["cases"] for row in recipes} != {
            "standard-v1": 16, "validation-v1": 128
        }:
            raise AssertionError("installed_recipe_catalog_mismatch")

        app = build_app(
            runs_dir=root / "runs", models_dir=root / "models", recipes_dir=root / "recipes"
        )
        with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
            for path in ("/", "/recipeManager", "/recipe-lab", "/static/benchmark.js",
                         "/static/benchmark.css"):
                response = client.get(path)
                if response.status_code != 200 or not response.content:
                    raise AssertionError(f"installed_dashboard_route_failed:{path}:{response.status_code}")
            response = client.get("/api/recipes")
            if (response.status_code != 200 or
                    {row["recipe_id"] for row in response.json()} !=
                    {"standard-v1", "validation-v1"}):
                raise AssertionError("installed_recipe_api_failed")

    print(f"installed_wheel_smoke_passed:{installed}")


if __name__ == "__main__":
    main()
