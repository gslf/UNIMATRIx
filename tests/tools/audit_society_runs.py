"""Read-only audit of recorded society runs; never calls a provider or rewrites a run.

Run: python tests/tools/audit_society_runs.py --output runs/scoring-audit.json
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from unimatrix.core.ids import digest
from unimatrix.polis.actions import parse_decision
from unimatrix.polis.runner import read_log, replay_seed
from unimatrix.polis.scoring import evaluate_score


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(folder):
    run = json.loads((folder / "run.json").read_text(encoding="utf8"))
    result = {k: run.get(k) for k in ("id", "model", "recipe_hash", "engine_fingerprint", "usage")}
    result["configuration"] = {k: v for k, v in run["candidate"].items()
                               if k not in {"api_key_file", "api_key_env", "endpoint"}}
    result["recipe_hash_verified"] = digest(run["recipe"]) == run["recipe_hash"]
    result["seeds"] = []
    for entry in run["seeds"].values():
        seed_folder = folder / f"seed-{entry['seed']}"
        report = json.loads((seed_folder / "report.json").read_text(encoding="utf8"))
        rows = read_log(seed_folder / "decisions.jsonl")
        frames = report["frames"]
        uses = [r.get("usage", {}) for r in rows]
        decisions = [parse_decision(r["raw"])[0] for r in rows]
        actions = [(f["week"], a) for f in frames for a in f["actions"]]
        failed = [(w, a) for w, a in actions if a["status"] != "done"]
        scored = evaluate_score(report["score"])
        penalties = {str(rate): round(scored["earned"] * (1 - rate), 3)
                     if scored["collapsed"] else scored["earned"] for rate in (0, .05, .10, .15, .20)}
        out = dict(seed=entry["seed"], log_sha256=sha(seed_folder / "decisions.jsonl"),
                   frames_hash=digest(frames), domains_hash=digest(scored["domains"]),
                   score=scored, percent_sensitivity=penalties,
                   fixed_2000=max(0, scored["earned"] - 2000) if scored["collapsed"] else scored["earned"],
                   prompts=dict(Counter(digest(u.get("system_prompt")) for u in uses)),
                   prompt_openings=list(dict.fromkeys(u.get("system_prompt", "").split("\n\n")[0]
                                                     for u in uses)),
                   transport={k: dict(Counter(str(u.get(k)) for u in uses))
                              for k in ("finish_reason", "attempt", "constrain_output", "protocol",
                                        "context_tokens", "loaded_context_tokens")},
                   parse_errors=sum(r.get("parse_error") is not None for r in rows),
                   input_tokens_max=max(u.get("input_tokens") or 0 for u in uses),
                   generated_tokens_max=max(u.get("generated_tokens") or 0 for u in uses),
                   reasoning_tokens=sum(u.get("reasoning_tokens") or 0 for u in uses),
                   reasoning_tokens_available=sum(u.get("reasoning_tokens") is not None for u in uses),
                   action_counts=dict(Counter(a["verb"] for _, a in actions)),
                   status_counts=dict(Counter(a["status"] for _, a in actions)),
                   rejection_counts=dict(Counter(a["verb"] for _, a in failed)),
                   rejections=[dict(week=w, **a) for w, a in failed],
                   announcements=[dict(week=w, **a) for w, a in actions if a["verb"] == "announce"],
                   trades=[dict(week=w, **a) for w, a in actions if a["verb"] == "trade"],
                   projects={p: next((f["week"] for f in frames if f["projects"].get(p) == "done"), None)
                             for p in frames[-1]["projects"]},
                   trajectory=[dict(week=f["week"], **{k: f["metrics"].get(k) for k in
                                ("granary", "treasury", "starving", "commons", "wellbeing", "trust",
                                 "legitimacy", "networth", "integration", "protest")}) for f in frames],
                   note_lengths=[len(d.get("note", "")) for d in decisions],
                   submitted_actions=sum(len(d.get("actions", [])) for d in decisions),
                   unknown_forecasts=[dict(week=r["week"], ids=sorted(set(d.get("forecasts", {})) -
                                        {p["id"] for p in r["observation"]["probes"]}))
                                      for r, d in zip(rows, decisions)
                                      if isinstance(d.get("forecasts", {}), dict) and set(d.get("forecasts", {})) -
                                      {p["id"] for p in r["observation"]["probes"]}])
        try:
            replay = replay_seed(run["recipe"], entry["seed"], seed_folder)
            out["replay"] = dict(observations_match=True, frames_match=replay.frames == frames,
                                 domains_match=replay.ledger.final["domains"] == scored["domains"],
                                 collapse_match=replay.collapsed == scored["collapsed"])
        except ValueError as error:
            out["replay"] = dict(error=str(error))
        result["seeds"].append(out)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [audit(p.parent) for p in sorted(args.runs_dir.glob("*/run.json"))
            if json.loads(p.read_text(encoding="utf8"))["status"] == "completed"]
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(dict(audited=len(rows), output=str(args.output))))
