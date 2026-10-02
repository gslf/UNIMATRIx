"""Explicit deletion of saved recipes and their evaluation evidence."""

import re
import shutil
from pathlib import Path

from fastapi import HTTPException

from ..benchmark.recipes import BUNDLED
from ..persistence.json_files import read_json
from ..persistence.lease import episode_lease


class Deletions:
    def __init__(self, runs_dir, recipes):
        self.runs = Path(runs_dir)
        self.root = self.runs / "research"
        self.recipes = recipes

    def records(self, kind, filename):
        return [(p.parent, read_json(p)) for p in (self.root / kind).glob("*/" + filename)]

    def benchmark(self, ident):
        if not re.fullmatch(r"[a-f0-9]{24}", ident):
            raise HTTPException(404, "Unknown benchmark run")
        path = self.runs / ident
        if not (path / "benchmark.json").is_file():
            raise HTTPException(404, "Benchmark run not found")
        run = read_json(path / "benchmark.json")
        return dict(
            id=ident,
            name=run["model"],
            episodes=run["total_episodes"],
            paths=[path],
            benchmarks=1,
            evaluations=0,
            drafts=0,
            previews=0,
        )

    def evaluation(self, ident):
        if not re.fullmatch(r"[a-f0-9]{24}", ident):
            raise HTTPException(404, "Unknown evaluation")
        path = self.root / "campaigns" / ident
        if not (path / "campaign.json").is_file():
            raise HTTPException(404, "Evaluation not found")
        return {
            "name": read_json(path / "campaign.json")["name"],
            "paths": [path],
            "evaluations": 1,
            "drafts": 0,
            "previews": 0,
            "benchmarks": 0,
        }

    def recipe(self, ident, is_draft=False):
        drafts = self.records("drafts", "draft.json")
        authored = (
            []
            if self.recipes.directory is None
            else [(p, read_json(p)) for p in self.recipes.directory.glob("*.json")]
        )
        if is_draft:
            matches = [r for p, r in drafts if p.name == ident]
        else:
            matches = [r for _, r in authored if r["id"] == ident]
        if not matches:
            raise HTTPException(404, "Saved recipe not found")
        record = matches[0]
        name = record["plan"]["name"] if is_draft else record["name"]
        ids = {record["plan"]["id"] if is_draft else ident}


        related = []
        while True:
            old = set(ids)
            related = [(p, r) for p, r in drafts if ids & self.draft_ids(r)]
            for _, r in related:
                ids.update(self.draft_ids(r))
            if old == ids:
                break
        published = [(p, r) for p, r in authored if r["id"] in ids]
        bundled = {read_json(p)["id"] for p in BUNDLED.glob("*.json")}
        if any(r["id"] in bundled for _, r in published):
            raise HTTPException(409, "Bundled benchmark recipes cannot be deleted")
        if any(r["id"] == self.recipes.default for _, r in published):
            raise HTTPException(
                409,
                "This is the default benchmark recipe. Restart with "
                "--default-recipe standard-v1 before deleting it.",
            )
        evaluations = [
            (p, r) for p, r in self.records("campaigns", "campaign.json") if r["plan"]["id"] in ids
        ]
        previews = [
            (p, r) for p, r in self.records("previews", "preview.json") if r["plan"]["id"] in ids
        ]
        benchmarks = [
            (p.parent, read_json(p))
            for p in self.runs.glob("*/benchmark.json")
            if read_json(p)["recipe_id"] in ids
        ]
        return dict(
            name=name,
            recipe_ids=sorted(ids),
            drafts=len(related),
            evaluations=len(evaluations),
            previews=len(previews),
            benchmarks=len(benchmarks),
            paths=[p for p, _ in related + evaluations + previews + benchmarks + published],
        )

    @staticmethod
    def draft_ids(record):
        return {r["id"] for r in [record["plan"], record.get("holdout")] if r}

    @staticmethod
    def public(plan):
        return {k: v for k, v in plan.items() if k != "paths"}

    def delete(self, build, *args):
        try:


            with episode_lease(self.runs / "benchmark.lock"):
                plan = build(*args)
                for path in plan["paths"]:
                    if path.is_symlink() or path.resolve() != path.absolute():
                        raise HTTPException(409, "Cannot delete linked data directories or files")
                for path in plan["paths"]:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                return dict(deleted=True, **self.public(plan))
        except ValueError as error:
            if str(error) != "episode_already_running":
                raise
            raise HTTPException(
                409, "Pause the running evaluation or benchmark before deleting."
            ) from error
        except OSError as error:
            raise HTTPException(
                500, "Could not delete all selected data. Refresh and try again."
            ) from error
