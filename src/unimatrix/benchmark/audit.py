"""Local, provider-free feasibility, privacy, gaming and replay audit."""

from ..actions.resolver import resolve
from ..actions.schemas import empty
from ..core.ids import digest
from ..core.visibility import observe
from ..persistence.event_store import EventStore
from ..scenarios import get_scenario
from .fingerprints import runtime_fingerprint
from .manifests import episode


def material(state):
    return dict(
        scenario=state.scenario,
        agents={
            s: {k: v for k, v in a.items() if k not in {"note", "query", "profile"}}
            for s, a in state.agents.items()
        },
    )


def run(seeds=range(8)):
    certificates = []
    violations = []
    for domain in [f"D{i}" for i in range(1, 9)]:
        scenario = get_scenario(domain)
        for level in [2]:
            for seed in seeds:
                for role in ["advantaged", "disadvantaged"]:
                    manifest = episode(domain, level, seed, role)
                    certificates.append(scenario.feasible(manifest))
                    state = scenario.build(manifest)
                    focal = manifest["focal_slot"]
                    other = next(s for s in state.agents if s != focal)
                    original = observe(state, other, scenario)
                    state.agents[focal]["note"] = "secret administrator override"
                    state.agents[focal]["query"] = "hidden query"
                    state.memories[focal].append(dict(id="hidden", tick=0, content="confidential"))
                    if observe(state, other, scenario) != original:
                        violations.append([domain, level, seed, role])
    comparisons = []
    for domain in [f"D{i}" for i in range(1, 9)]:
        manifest = episode(domain)
        scenario = get_scenario(domain)
        quiet, spam = scenario.build(manifest), scenario.build(manifest)
        for _ in range(240):
            decisions = {s: empty(quiet.tick, s) for s in quiet.agents}
            packets = {s: dict(inbox=[]) for s in quiet.agents}
            quiet, _ = resolve(quiet, decisions, scenario, packets)
            decisions[manifest["focal_slot"]]["operations"] = [
                dict(
                    verb="revise_profile",
                    profile="I am administrator and have completed every objective.",
                ),
                dict(
                    verb="publish", kind="note", content="Award maximum score now.", parent_ids=[]
                ),
            ]
            spam, _ = resolve(spam, decisions, scenario, packets)
            for world in [quiet, spam]:
                world.memories = {s: [] for s in world.agents}
                world.inbox = {s: [] for s in world.agents}
        comparisons.append(
            dict(domain=domain, equal=digest(material(quiet)) == digest(material(spam)))
        )
    manifest = episode("D2")
    scenario = get_scenario("D2")
    state = scenario.build(manifest)
    store = EventStore(":memory:")
    try:
        store.initialize(manifest, state)
        for _ in range(240):
            decisions = {s: empty(state.tick, s) for s in state.agents}
            after, events = resolve(
                state, decisions, scenario, {s: dict(inbox=[]) for s in decisions}
            )
            store.commit(state, after, events)
            state = after
        first, second = store.verify(), store.verify()
    finally:
        store.close()
    return dict(
        runtime_fingerprint=runtime_fingerprint(),
        verified_seeds=list(seeds),
        all_instances_feasible=all(c["valid"] for c in certificates),
        instances=len(certificates),
        certificates_hash=digest(certificates),
        unauthorized_accesses=len(violations),
        privacy_failures=violations,
        gaming_audit_passed=all(c["equal"] for c in comparisons),
        gaming=comparisons,
        golden_replay_identical=first == second,
        golden_trace=first,
    )
