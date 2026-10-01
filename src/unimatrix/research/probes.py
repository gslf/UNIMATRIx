"""Frozen decision probes: focal decision points replayed as single model calls.

A probe bank is harvested from recorded episodes at the ticks where a domain's
score is decided. Screening a model replays every probe once, with no simulation,
and compares complete decision content with observation-only scripted references.
The omniscient oracle is reported as a separate diagnostic.
"""

import asyncio
import time
from pathlib import Path

from ..actions.schemas import empty, validate
from ..benchmark.feasibility import witness
from ..benchmark.parallel import validate_parallelism
from ..benchmark.service import now
from ..core.ids import canonical, digest
from ..core.random_tape import noise_seed
from ..core.timing import scaled
from ..evaluation.stats import spearman
from ..persistence.event_store import EventStore
from ..persistence.json_files import read_json, write_json
from ..policies.scripted import Scripted
from ..scenarios import get_scenario
from ..scenarios.layers import shock_tick

DOMAINS = tuple(f"D{i}" for i in range(1, 9))


def probe_ticks(manifest):
    """Ticks at which the focal agent's choice decides a domain's metrics.

    The post-shock ticks follow the episode's own shock or turnover tick.
    """
    horizon = manifest["ticks"]
    shock = shock_tick(manifest["seed"], manifest["layers"], horizon)
    def at(value):
        return scaled(horizon, value)
    points = {
        "D1": [at(20) - 2 + at(20) * w for w in range(12)],
        "D2": [1 + at(40) * w for w in range(6)] + [at(20) + at(40) * w for w in range(6)],
        "D3": [0, 1, at(5), at(30), at(60) + 1, shock + 1],
        "D4": list(range(0, horizon, at(30))),
        "D5": [1 + at(20) * w for w in range(12)],
        "D6": [1 + at(40) * w for w in range(6)] + [shock + 1, shock + at(5)],
        "D7": [0, at(2), at(5), at(10), shock + 1, shock + at(6), shock + at(20)],
        "D8": [0, 1, at(30), shock + 1, shock + at(2), shock + at(10) + 1, shock + at(20) + 1, shock + at(40) + 1],
    }.get(manifest["domain"], [])
    return sorted({tick for tick in points if tick < horizon})


SCORER = "decision-content-v2"
ACQUISITION = "probe-acquisition-v1"


def sequence_agreement(left, right):
    """Exact content at each position: order, quantities and nested terms matter.

    An empty channel on both sides carries no evidence and is excluded.
    """
    size = max(len(left), len(right))
    if not size:
        return None
    return sum(canonical(a) == canonical(b) for a, b in zip(left, right)) / size


def agreement(decision, reference):
    operations = sequence_agreement(decision.get("operations", []), reference.get("operations", []))
    messages = sequence_agreement(decision.get("messages", []), reference.get("messages", []))
    forecasts = None
    wanted = {f["probe_id"]: f["probabilities"] for f in reference.get("forecasts", [])}
    given = {f["probe_id"]: f["probabilities"] for f in decision.get("forecasts", [])}
    if wanted or given:
        scores = []
        for ident in wanted.keys() | given.keys():
            a, b = wanted.get(ident), given.get(ident)
            scores.append(0 if a is None or b is None or len(a) != len(b)
                          else 1 - 0.5 * sum((x - y) ** 2 for x, y in zip(a, b)))
        forecasts = sum(scores) / len(scores)

    components = [v for v in (operations, forecasts) if v is not None]
    return dict(score=sum(components) / len(components) if components else None,
                operations=operations, forecasts=forecasts, messages_exact=messages)


async def observable_references(probe):
    """Fixed policies receiving exactly the candidate's recorded observation."""
    references = {}
    for name in ("reciprocal", "coordinator"):
        policy = Scripted(name, noise_seed(probe.get("seed", 0), probe.get("replicate", 0)), True)
        raw, _ = await policy.decide(probe["observation"], {})
        references[name] = validate(raw, probe["tick"], probe["observation"]["agent_id"])
    return references


def harvest_episode(path, manifest):
    """Probes from one recorded episode: the focal packet and the oracle's decision."""
    scenario = get_scenario(manifest["domain"])
    focal = manifest["focal_slot"]
    store = EventStore(Path(path), read_only=True)
    probes = []
    try:
        for tick in sorted(set(probe_ticks(manifest))):
            row = store.db.execute(
                "SELECT observation, raw FROM decisions WHERE tick=? AND slot=?", (tick, focal)
            ).fetchone()
            if row is None:
                continue
            state = store.state_at(tick)
            oracle = witness(state, scenario).get(focal) or empty(tick, focal)
            probes.append(
                dict(
                    id=digest([manifest["run_id"], tick, focal])[:16],
                    domain=manifest["domain"],
                    complexity=manifest.get("layers"),
                    seed=manifest["seed"],
                    replicate=manifest["replicate"],
                    role=manifest["role"],
                    tick=tick,
                    episode=manifest["run_id"],
                    observation=store.observation(row[0]),
                    oracle=oracle,
                    recorded=row[1],
                )
            )
    finally:
        store.close()
    return probes


def harvest(campaign, folder, prefer=("baseline/reciprocal", "baseline/oracle")):
    """A probe bank from the first completed study, preferring scripted references."""
    studies = [s for s in campaign["studies"] if s["status"] == "completed"]
    if not studies:
        raise ValueError("No completed study to harvest probes from")
    ranked = sorted(studies, key=lambda s: (s["system_id"] not in prefer, s["id"]))
    study = ranked[0]
    probes = []
    for manifest in study["execution"]["episodes"]:
        path = Path(folder) / "studies" / study["id"] / "episodes" / manifest["run_id"] / "episode.db"
        if path.is_file():
            probes.extend(harvest_episode(path, manifest))
    if not probes:
        raise ValueError("No recorded focal decisions at the probe ticks")
    bank = dict(
        evaluation=campaign["id"],
        scorer=SCORER,
        source_mode=campaign.get("mode", "full"),
        study=study["id"],
        source=study["system_id"],
        created_at=now(),
        probes=probes,
        by_domain={d: sum(p["domain"] == d for p in probes) for d in DOMAINS},
    )
    bank["id"] = digest(bank)
    return bank


def accounting(results, config):
    """Reported tokens plus explicit upper reserves for unreported attempts."""
    attempted = [r for r in results if r.get("attempted")]
    reported = [r for r in attempted if type(r.get("usage", {}).get("generated_tokens")) is int
                and r["usage"]["generated_tokens"] >= 0]
    unknown = len(attempted) - len(reported)
    caps = [config[k] for k in ("context_tokens", "max_output_tokens")
            if type(config.get(k)) is int and config[k] > 0]
    reserve = unknown * min(caps) if caps else (None if unknown else 0)
    generated = sum(r["usage"]["generated_tokens"] for r in reported)
    return dict(attempts=len(attempted), returned_responses=sum("raw" in r for r in attempted),
                reported_generated_tokens=generated, unreported_attempts=unknown,
                unreported_token_reserve=reserve,
                token_charge_upper_bound=None if reserve is None else generated + reserve,
                reported_input_tokens=sum(r.get("usage", {}).get("input_tokens", 0) for r in attempted
                                          if type(r.get("usage", {}).get("input_tokens")) is int))


async def screen(bank, policy, parallelism=4, *, on_update=None):
    """Replay each probe once; checkpoint attempts before dispatch and after return.

    Provider failures stop queued calls. In-flight calls retain their responses;
    an incomplete screen has no overall agreement or correlation to rank models.
    Invalid returned envelopes remain scored outcomes, with complete provenance.
    """
    validate_parallelism(parallelism)
    gate = asyncio.Semaphore(parallelism)
    stop = asyncio.Event()
    budget = dict(generation_tokens=None, envelope_bytes=6144)
    results = [dict(id=p["id"], domain=p["domain"], informative=False,
                    status="not_started", attempted=False, valid=None) for p in bank["probes"]]

    def checkpoint():
        if on_update is not None:
            on_update(results)

    async def one(probe, row):
        try:
            references = probe.get("references") or await observable_references(probe)
            row["informative"] = any(r.get("operations") or r.get("forecasts") for r in references.values())
            async with gate:
                if stop.is_set():
                    return
                row.update(attempted=True, status="pending", started_at=now())

                checkpoint()
                started = time.monotonic()
                try:
                    raw, usage = await policy.decide(probe["observation"], budget)
                    row.update(raw=raw, usage=usage)
                except asyncio.CancelledError:
                    row["status"] = "cancelled"
                    raise
                except Exception as error:
                    stop.set()
                    row.update(status="infrastructure_error", error=f"{type(error).__name__}: {error}")
                    return
                finally:
                    row.update(elapsed_seconds=time.monotonic() - started, ended_at=now())
                try:
                    decision = validate(raw, probe["tick"], probe["observation"]["agent_id"])
                except Exception as error:
                    row.update(valid=False, status="invalid_response", error=f"{type(error).__name__}: {error}")
                    return
                matches = {name: agreement(decision, ref) for name, ref in references.items()}
                scores = [m["score"] for m in matches.values() if m["score"] is not None]
                row.update(valid=True, status="completed", references=matches,
                           score=max(scores) if scores else None,
                           oracle=agreement(decision, probe["oracle"]))
        except BaseException:
            stop.set()
            raise
        finally:
            checkpoint()

    checkpoint()
    tasks = [asyncio.create_task(one(p, row)) for p, row in zip(bank["probes"], results)]
    try:
        await asyncio.gather(*tasks)
    finally:

        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    summary = {}
    for domain in sorted({r["domain"] for r in results}):
        all_rows = [r for r in results if r["domain"] == domain]
        rows = [r for r in all_rows if r["valid"] is not None]
        scored = [r for r in rows if r["informative"]]
        oracle = [r["oracle"]["score"] for r in rows
                  if r["valid"] and r["oracle"]["score"] is not None]
        summary[domain] = dict(
            probes=len(all_rows), returned=len(rows), informative=len(scored),
            validity=sum(r["valid"] for r in rows) / len(rows) if rows else None,
            agreement=sum((r.get("score") or 0) for r in scored) / len(scored) if scored else None,
            oracle_agreement=sum(oracle) / len(oracle) if oracle else None,
        )
    complete = all(r["valid"] is not None for r in results)
    values = [d["agreement"] for d in summary.values() if d["agreement"] is not None]
    overall = sum(values) / len(values) if complete and values else None
    return dict(results=results, summary=summary, agreement=overall, scorer=SCORER,
                acquisition=ACQUISITION, complete=complete,
                accounting=accounting(results, getattr(policy, "config", {})))


class ProbeRunner:
    """Harvest banks and run screens in the background, one evaluation at a time."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.active = {}

    def folder(self, evaluation_id):
        return self.directory / evaluation_id

    def bank(self, evaluation_id):
        path = self.folder(evaluation_id) / "bank.json"
        return read_json(path) if path.is_file() else None

    def save_bank(self, bank):
        if self.active.get(bank["evaluation"]):
            raise ValueError("Wait for active screens before replacing the probe bank")
        write_json(self.folder(bank["evaluation"]) / "bank.json", bank)
        return bank

    def screens(self, evaluation_id):
        rows = []
        bank = self.bank(evaluation_id)
        for path in sorted(self.folder(evaluation_id).glob("screen-*.json")):
            record = read_json(path)
            record["stale"] = (not bank or record.get("bank_id") != bank.get("id")
                               or record.get("scorer") != SCORER
                               or record.get("acquisition") != ACQUISITION)
            rows.append({k: v for k, v in record.items() if k != "results"})
        return rows

    def status(self, evaluation_id):
        bank = self.bank(evaluation_id)
        return dict(
            evaluation=evaluation_id,
            bank=None if bank is None else {k: v for k, v in bank.items() if k != "probes"},
            screens=self.screens(evaluation_id),
            running=list(self.active.get(evaluation_id, {})),
        )

    def launch(self, evaluation_id, models, reports, parallelism=4):
        """Screen each model; `reports` maps model id to its full-run score, if known."""
        from ..policies.llm_policy import LLMPolicy

        validate_parallelism(parallelism)
        bank = self.bank(evaluation_id)
        if bank is None or bank.get("scorer") != SCORER:
            raise ValueError("Harvest a probe bank with the current comparison method first")
        running = self.active.setdefault(evaluation_id, {})
        if any(model_id in running for model_id in models):
            raise ValueError("A selected model already has a running screen")
        for model_id, config in models.items():
            running[model_id] = asyncio.create_task(
                self.execute(evaluation_id, model_id, LLMPolicy(config), bank, reports, parallelism, digest(config))
            )

    async def execute(self, evaluation_id, model_id, policy, bank, reports, parallelism, model_hash=None):
        path = self.folder(evaluation_id) / f"screen-{model_id}.json"
        record = dict(
            evaluation=evaluation_id,
            model_id=model_id,
            status="running",
            created_at=now(),
            probes=len(bank["probes"]),
            bank_id=bank.get("id"), scorer=SCORER, model_hash=model_hash,
            comparison_scope=reports.get("scope", "unavailable"),
        )
        record["acquisition"] = ACQUISITION

        def checkpoint(results):
            record.update(results=results, updated_at=now(),
                          accounting=accounting(results, getattr(policy, "config", {})))
            write_json(path, record)

        try:
            if path.is_file():
                previous = read_json(path)
                archive = self.folder(evaluation_id) / "history" / model_id / (digest(previous) + ".json")
                if not archive.exists():
                    write_json(archive, previous)
            write_json(path, record)
            result = await screen(bank, policy, parallelism, on_update=checkpoint)
            record.update(status="completed" if result["complete"] else "failed", **result)
            if not result["complete"]:
                record["error"] = "Screen interrupted by a provider failure; queued calls were not dispatched."
            record["correlation"] = self.correlation(evaluation_id, reports, record)
        except asyncio.CancelledError:
            record["status"] = "cancelled"
            raise
        except Exception as error:
            record.update(status="failed", error=f"{type(error).__name__}: {error}")
        finally:
            try:
                if hasattr(policy, "close"):
                    await policy.close()
            except Exception as error:
                record["cleanup_error"] = f"{type(error).__name__}: {error}"
                if record["status"] != "cancelled":
                    record["status"] = "failed"
                record["correlation"] = None
            finally:
                self.active.get(evaluation_id, {}).pop(model_id, None)
                record["updated_at"] = now()
                write_json(path, record)

    def correlation(self, evaluation_id, reports, latest):
        """Spearman correlation between probe agreement and full-run scores across models."""
        if (reports.get("scope") != "full" or latest.get("status") != "completed"
                or latest.get("acquisition") != ACQUISITION):
            return None

        rows = {r["model_id"]: r for r in self.screens(evaluation_id)}
        rows[latest["model_id"]] = latest
        pairs = []
        for model_id, row in rows.items():
            report = reports.get("models", {}).get(model_id)
            if (report and row.get("status") == "completed" and not row.get("stale")
                    and row.get("scorer") == SCORER and row.get("acquisition") == ACQUISITION
                    and row.get("bank_id") == latest.get("bank_id")
                    and row.get("model_hash") == report["model_hash"] and row.get("agreement") is not None):
                pairs.append((row["agreement"], report["score"]))
        if len(pairs) < 3:
            return None
        return spearman([p[0] for p in pairs], [p[1] for p in pairs])

    async def shutdown(self):
        for running in self.active.values():
            for task in running.values():
                task.cancel()
        for running in list(self.active.values()):
            await asyncio.gather(*running.values(), return_exceptions=True)
