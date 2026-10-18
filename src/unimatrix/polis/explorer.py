"""Export society runs as xCDN files for the static Explorer (`explorer/`).

xCDN (eXtensible Cognitive Data Notation, https://github.com/gslf/xCDN) is used for its
native types (exact decimals for scores, timestamps, durations), semantic tags and streams:
a run file holds one `#run` value followed by a stream of `#week` values, one per seed and
week; `index.xcdn` holds an `#explorer` header followed by one `#entry` per run.

The Explorer never talks to the application: it only reads these files.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from .catalog import DOMAINS

SCHEMA = "https://gslf.github.io/xCDN/schemas/v1/meta.xcdn"
RUN_FORMAT = "unimatrix.explorer.run/1"
INDEX_FORMAT = "unimatrix.explorer.index/1"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
RESIDENT_COLUMNS = ("id", "status", "activity", "wb", "st", "lo", "gr", "tr", "lg", "hp", "coins", "food",
                    "protest", "sick", "hungry", "concern", "intensity", "task")


# ----------------------------------------------------------------------------- xCDN writer
@dataclass(frozen=True)
class Tagged:
    tag: str
    value: object


@dataclass(frozen=True)
class Annotated:
    name: str
    args: tuple
    value: object


@dataclass(frozen=True)
class Raw:
    """Exact decimal text, written as d"..."."""
    text: str


def decimal(value, places=3):
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-places)
    return Raw(str(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)))


def key(name):
    return name if IDENT.fullmatch(name) and name not in ("true", "false", "null") else json.dumps(name)


def scalar(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "null"
        text = repr(round(value, 4))
        return text if ("." in text or "e" in text or "E" in text) else text + ".0"
    if isinstance(value, Raw):
        return f'd"{value.text}"'
    if isinstance(value, Decimal):
        return f'd"{value}"'
    if isinstance(value, datetime):
        stamp = value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return f't"{stamp}"'
    if isinstance(value, timedelta):
        seconds = max(0, int(value.total_seconds()))
        hours, rest = divmod(seconds, 3600)
        minutes, secs = divmod(rest, 60)
        text = "PT" + (f"{hours}H" if hours else "") + (f"{minutes}M" if minutes else "") + f"{secs}S"
        return f'r"{text}"'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"cannot write {type(value).__name__} as xCDN")


def dumps(value, indent=0, step=2, inline=False):
    """Serialize a Python value as xCDN text; `inline` writes it on one line."""
    pad, inner = " " * indent, " " * (indent + step)
    if isinstance(value, Tagged):
        return f"#{value.tag} " + dumps(value.value, indent, step, inline)
    if isinstance(value, Annotated):
        args = ", ".join(dumps(a, 0, step, True) for a in value.args)
        return f"@{value.name}({args}) " + dumps(value.value, indent, step, inline)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{key(str(k))}: {dumps(v, indent + step, step, inline)}" for k, v in value.items()]
        if inline:
            return "{ " + ", ".join(items) + " }"
        return "{\n" + "".join(f"{inner}{item},\n" for item in items) + pad + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = [dumps(v, indent + step, step, inline) for v in value]
        if inline or sum(len(i) for i in items) < 100 and all("\n" not in i for i in items):
            return "[" + ", ".join(items) + "]"
        return "[\n" + "".join(f"{inner}{item},\n" for item in items) + pad + "]"
    return scalar(value)


def document(directives, values, header=""):
    lines = [f"// {line}" if line else "//" for line in header.splitlines()] if header else []
    lines += [f"${name}: {scalar(text)}," for name, text in directives]
    lines.append("")
    for value, comment in values:
        if comment:
            lines.append(f"// {comment}")
        lines.append(dumps(value[0], inline=value[1]) if isinstance(value, tuple) else dumps(value))
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- run → xCDN
def parse_time(text):
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def resident_row(r):
    concern = r.get("concern") or {}
    return [r["id"], r["status"], r["activity"], r["wb"], r["st"], r["lo"], r["gr"], r["tr"], r.get("lg", 0.0),
            r["hp"], r["coins"], r["food"], r["protest"], r["sick"], r["hungry"], concern.get("kind"),
            concern.get("intensity"), r.get("task", "work")]


def action_value(a):
    params = {k: v for k, v in (a.get("params") or {}).items() if isinstance(v, (str, int, float, bool))}
    body = dict(verb=a.get("verb"), status=a.get("status"), targets=list(a.get("targets") or []),
                place=a.get("place"), say=a.get("say"), text=a.get("text"), label=a.get("label"), params=params,
                failure=a.get("failure"), error=a.get("error"))
    return Tagged("action", {k: v for k, v in body.items() if v not in (None, [], {})})


def decision_value(row):
    """Everything the model did in one week: what it read, what it answered, how it reasoned."""
    from .briefing import render

    usage = row.get("usage") or {}
    latency = usage.get("latency")
    body = dict(
        briefing=render(row["observation"]) if row.get("observation") else None,
        answer=row.get("raw"),
        reasoning=usage.get("reasoning") or None,
        error=row.get("parse_error"),
        input_tokens=usage.get("input_tokens"),
        generated_tokens=usage.get("generated_tokens"),
        reasoning_tokens=usage.get("reasoning_tokens"),
        finish_reason=usage.get("finish_reason"),
        latency=decimal(latency, 3) if isinstance(latency, (int, float)) else None,
        retries=usage.get("attempt") or None,
    )
    return Tagged("decision", {k: v for k, v in body.items() if v not in (None, "")})


def week_value(seed, frame, decision=None):
    metrics = {k: v for k, v in frame["metrics"].items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    body = dict(
        seed=seed, week=frame["week"], season=frame.get("season"),
        metrics=metrics,
        points={d: decimal(v, 2) for d, v in (frame.get("points") or {}).items()},
        stores=frame.get("stores", {}),
        commons=frame.get("commons", {}),
        skills=frame.get("skills", {}),
        beliefs=frame.get("beliefs", {}),
        conventions=frame.get("conventions", {}),
        projects=frame.get("projects", {}),
        conflicts={k: dict(c) for k, c in (frame.get("conflicts") or {}).items()},
        story=sorted(k for k, s in (frame.get("story") or {}).items() if s.get("activated") == frame["week"]),
        residents=Annotated("columns", RESIDENT_COLUMNS, [resident_row(r) for r in frame["residents"]]),
        ties=frame.get("ties", []),
        events=frame.get("events", []),
        actions=[action_value(a) for a in frame.get("actions") or []],
    )
    steward = frame.get("steward")
    if steward:
        body["steward"] = {k: v for k, v in steward.items() if v not in (None, "", {})}
    if decision:
        body["decision"] = decision_value(decision)
    if frame.get("collapsed"):
        body["collapsed"] = Tagged("collapse", frame["collapsed"])
    return Tagged("week", body)


def run_value(run, recipe, seeds, references):
    if run.get("kind", "model") != "model":
        raise ValueError("Explorer accepts model runs only")
    score = run.get("score") or {}
    first = next(iter(seeds.values()))
    usage = run.get("usage") or {}
    created, updated = parse_time(run.get("created_at")), parse_time(run.get("updated_at"))
    domains = {}
    for d, meta in DOMAINS.items():
        per_seed = [s["score"]["domains"][d] for s in seeds.values()]
        domains[d] = Tagged("domain", dict(
            name=meta["name"], unit=meta["unit"],
            points=decimal(sum(x["points"] for x in per_seed) / len(per_seed), 3),
            units=decimal(sum(x["units"] for x in per_seed) / len(per_seed), 2),
            rate=per_seed[0]["rate"]))
    seed_rows = []
    for seed, report in seeds.items():
        s = report["score"]
        seed_rows.append(Tagged("seed", dict(
            seed=int(seed), total=decimal(s["total"], 3), earned=decimal(s["earned"], 3),
            scoring_rule=s.get("rule"), penalty=decimal(s["penalty"], 3),
            penalty_rate=s.get("penalty_rate"),
            survived_weeks=s["survived_weeks"],
            collapsed=Tagged("collapse", s["collapsed"]) if s["collapsed"] else None,
            wellby=decimal(s["wellby"], 2), events=s["events"],
            narrative=report["narrative"],
            diagnosis=[Tagged("finding", f) for f in report["diagnosis"]],
            path=[Tagged("step", p) for p in report["story"]["path"]],
            outcomes=[Tagged("outcome", {k: v for k, v in o.items() if k != "reward"}
                             | {"reward": {k: decimal(v, 1) for k, v in (o.get("reward") or {}).items()}})
                      for o in report["story"]["outcomes"]],
            actions=report.get("actions", {}), action_audit=report.get("action_audit"))))
    nodes = []
    for n in recipe["story"]["nodes"]:
        position = n.get("position") or {}
        nodes.append(Tagged("node", dict(
            id=n["id"], title=n["title"], kind=n["kind"], x=position.get("x"), y=position.get("y"),
            outcome=(n["outcome"] or {}).get("type"), label=(n["outcome"] or {}).get("label"))))
    edges = [[n["id"], e["to"], e.get("label", "")] for n in recipe["story"]["nodes"] for e in n["edges"]]
    body = dict(
        id=run["id"], kind="model", model=run["model"], snapshot=run.get("snapshot", ""),
        model_id=run.get("model_id"), status=run["status"],
        created=created, finished=updated,
        duration=timedelta(seconds=float(usage.get("seconds") or 0)),
        usage=dict(calls=usage.get("calls", 0), input_tokens=usage.get("input_tokens", 0),
                   generated_tokens=usage.get("generated_tokens", 0)),
        recipe=dict(id=recipe["id"], name=recipe["name"], revision=run.get("recipe_hash", "")[:12],
                    description=recipe["description"], weeks=recipe["ticks"], seeds=recipe["seeds"],
                    actions_per_week=recipe["steward"]["actions_per_week"], world=recipe["world"]["name"],
                    start_season=recipe["world"]["start_season"],
                    season_length=recipe["world"]["season_length"],
                    commons=dict(name=recipe["world"]["commons"]["name"],
                                 capacity=recipe["world"]["commons"]["capacity"],
                                 critical=recipe["world"]["commons"]["critical"]),
                    collapse={k: v for k, v in recipe["collapse"].items()}),
        score=dict(total=decimal(score.get("total", first["score"]["total"]), 3),
                   rule=score.get("rule", first["score"].get("rule")),
                   earned=decimal(sum(s["score"]["earned"] for s in seeds.values()) / len(seeds), 3),
                   penalty=decimal(sum(s["score"]["penalty"]
                                       for s in seeds.values()) / len(seeds), 3),
                   collapsed_seeds=score.get("collapsed", sum(1 for s in seeds.values() if s["score"]["collapsed"])),
                   relative_to_expert=decimal(score.get("relative_to_expert"), 3),
                   wellby=decimal(score.get("wellby", first["score"]["wellby"]), 2),
                   domains=domains),
        references={k: decimal(v["total"] if isinstance(v, dict) else v, 3) for k, v in (references or {}).items()},
        factions=[Tagged("faction", f) for f in recipe["factions"]],
        residents=[Tagged("resident", dict(
            id=r["id"], name=r["name"], age=r["age"], role=r["role"], faction=r["faction"], newcomer=r["newcomer"],
            archetype=r["archetype"], bio=r.get("bio", ""), status=r["status"], ended=r["ended"],
            joined=r["joined"], wellbeing=r["wellbeing"], trust=r["trust"], skills=r["skills"],
            concern=(r["concern"] or {}).get("kind")))
            for r in first["residents"]],
        story=dict(nodes=nodes, edges=edges),
        seeds=seed_rows,
    )
    return Tagged("run", body)


def run_document(run, recipe, seeds, references, decisions=None):
    """The full xCDN text of one run: a `#run` header and a stream of `#week` values.

    `decisions` maps each seed to its decision log rows by week (model runs only)."""
    head = run_value(run, recipe, seeds, references)
    prompts = sorted({hashlib.sha256(row["usage"]["system_prompt"].encode("utf8")).hexdigest()
                      for rows in (decisions or {}).values() for row in rows.values()
                      if row.get("usage", {}).get("system_prompt")})
    candidate = run.get("candidate") or {}
    head.value["protocol"] = dict(engine_fingerprint=run.get("engine_fingerprint"),
                                  system_prompt_sha256=prompts,
                                  constrain_output=candidate.get("constrain_output"),
                                  budget_track=candidate.get("budget_track"))
    values = [(head, "The run: model, recipe, score, people and story graph")]
    for seed, report in seeds.items():
        logged = (decisions or {}).get(seed, {})
        failures = {(f["week"], f["index"]): f for f in report.get("action_audit", {}).get("failures", [])}
        for i, frame in enumerate(report.get("frames", [])):
            frame = dict(frame, actions=[dict(a, failure=failures.get((frame["week"], a.get("index", n))))
                                        for n, a in enumerate(frame.get("actions", []))])
            values.append(((week_value(int(seed), frame, logged.get(frame["week"])), True),
                           f"seed {seed}" if i == 0 else None))
    header = (f"UNIMATRIx Explorer · society run {run['id']}\n{run['model']} · {recipe['name']}\n"
              "xCDN stream: one #run value, then one #week value per seed and week\n"
              "(model runs: each #week carries the model's #decision: briefing, answer, reasoning, tokens).")
    return document([("schema", SCHEMA), ("format", RUN_FORMAT)], values, header)


def entry_value(run, recipe, seeds, references, file):
    score = run.get("score") or {}
    first = next(iter(seeds.values()))
    collapses = [s["score"]["collapsed"] for s in seeds.values() if s["score"]["collapsed"]]
    deaths = sum(s["score"]["events"]["deaths"] for s in seeds.values())
    end = first["timeline"][-1] if first.get("timeline") else {}
    return Tagged("entry", dict(
        run=run["id"], file=file, kind="model", model=run["model"], snapshot=run.get("snapshot", ""),
        recipe=recipe["id"], recipe_name=recipe["name"], revision=run.get("recipe_hash", "")[:12],
        created=parse_time(run.get("created_at")),
        total=decimal(score.get("total", first["score"]["total"]), 3),
        scoring_rule=score.get("rule", first["score"].get("rule")),
        earned=decimal(sum(s["score"]["earned"] for s in seeds.values()) / len(seeds), 3),
        penalty=decimal(sum(s["score"]["penalty"]
                            for s in seeds.values()) / len(seeds), 3),
        relative_to_expert=decimal(score.get("relative_to_expert"), 3),
        collapsed=len(collapses), seeds=len(seeds),
        collapse=Tagged("collapse", collapses[0]) if collapses else None,
        wellby=decimal(score.get("wellby", first["score"]["wellby"]), 2),
        domains={d: decimal(sum(s["score"]["domains"][d]["points"] for s in seeds.values()) / len(seeds), 3)
                 for d in DOMAINS},
        vitals=dict(population=end.get("population"), wellbeing=end.get("wellbeing"), trust=end.get("trust"),
                    commons=end.get("commons"), gini=end.get("gini"), deaths=deaths),
        references={k: decimal(v["total"] if isinstance(v, dict) else v, 3) for k, v in (references or {}).items()},
    ))


def index_document(count, generated=None):
    header = ("UNIMATRIx Explorer · index\nxCDN stream: one #explorer header, then one #entry per exported run.\n"
              "Read-only data for the static Explorer; regenerated whenever a run is exported.")
    head = Tagged("explorer", dict(
        generated=generated or datetime.now(timezone.utc), runs=count,
        domains={d: dict(name=m["name"], unit=m["unit"]) for d, m in DOMAINS.items()}))
    values = [(head, None)]
    return document([("schema", SCHEMA), ("format", INDEX_FORMAT)], values, header)


# ----------------------------------------------------------------------------- files
class ExplorerExport:
    """Writes `data/runs/<id>.xcdn` and regenerates `data/index.xcdn` under an Explorer folder."""

    def __init__(self, folder, data="data"):
        self.folder = Path(folder)
        self.data = self.folder / data

    def write_run(self, run, recipe, seeds, references, decisions=None):
        if not seeds:
            return None
        (self.data / "runs").mkdir(parents=True, exist_ok=True)
        path = self.data / "runs" / f"{run['id']}.xcdn"
        atomic_write(path, run_document(run, recipe, seeds, references, decisions))
        entry = entry_value(run, recipe, seeds, references, f"runs/{run['id']}.xcdn")
        atomic_write(self.data / "entries" / f"{run['id']}.xcdn",
                     document([("schema", SCHEMA), ("format", "unimatrix.explorer.entry/1")], [((entry, False), None)]))
        self.rebuild_index()
        self.sync_assets()
        return path

    def sync_assets(self):
        """Copy the village renderer the Explorer shares with the application, if it changed."""
        source = Path(__file__).resolve().parents[1] / "web" / "static" / "society-village.js"
        target = self.folder / "vendor" / "village.js"
        if source.is_file() and (not target.is_file() or target.read_bytes() != source.read_bytes()):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())

    def remove_run(self, ident):
        for path in (self.data / "runs" / f"{ident}.xcdn", self.data / "entries" / f"{ident}.xcdn"):
            if path.is_file():
                path.unlink()
        self.rebuild_index()

    def rebuild_index(self):
        """The index is the concatenation of the per-run entries (xCDN streams concatenate)."""
        entries = []
        for path in sorted((self.data / "entries").glob("*.xcdn")):
            text = path.read_text(encoding="utf8")
            body = "\n".join(line for line in text.splitlines() if not line.startswith(("$", "//")) and line.strip())
            entries.append(body)
        head = index_document(len(entries))
        atomic_write(self.data / "index.xcdn", head + "".join(f"{body}\n" for body in entries))


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf8")
    temporary.replace(path)


def recorded(folder, run):
    """The seed reports of a run folder and, per seed, its decision log rows by week."""
    from ..persistence.json_files import read_json
    from .runner import read_log

    seeds, decisions = {}, {}
    for entry in run["seeds"].values():
        path = Path(folder) / f"seed-{entry['seed']}" / "report.json"
        if path.is_file():
            seeds[str(entry["seed"])] = read_json(path)
            decisions[str(entry["seed"])] = {row["week"]: row for row in read_log(path.with_name("decisions.jsonl"))}
    return seeds, decisions


def export_run_folder(folder, explorer, references=None):
    """Export a completed run folder (runs/<id>) into the Explorer, with its decision logs."""
    from ..persistence.json_files import read_json

    run = read_json(Path(folder) / "run.json")
    seeds, decisions = recorded(folder, run)
    return ExplorerExport(explorer).write_run(run, run["recipe"], seeds, references, decisions)
