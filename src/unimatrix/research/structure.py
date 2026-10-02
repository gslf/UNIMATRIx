"""The effective social structure of one recipe case, for the Lab's relations view."""

from ..benchmark.recipes import bind_candidate
from ..benchmark.scheduler import society_overrides
from ..benchmark.validation import is_model, scripted_name
from ..policies.scripted import Scripted
from ..scenarios import get_scenario
from ..scenarios.base import role_of
from ..scenarios.layers import layers_key
from ..world.contracts import Rejected


def society_structure(spec, domain, seed=None, role=None, stratum=None, certify=False):
    """Build one case and report who knows what, who reaches whom and what ties agents."""
    cases = [
        c
        for c in spec["cases"]
        if c["domain"] == domain
        and seed in (None, c["seed"])
        and role in (None, c["role"])
        and stratum in (None, layers_key(c["layers"]))
    ]
    if not cases:
        raise ValueError("The recipe has no such case")
    single = dict(spec, cases=cases[:1], domains={domain: spec["domains"][domain]})
    manifest = bind_candidate(single, "passive")["episodes"][0]
    scenario = get_scenario(domain)
    state = scenario.build(manifest)
    base = scenario.structure(state)
    overrides = society_overrides(manifest)
    focal = manifest["focal_slot"]
    agents = []
    for slot in manifest["slots"]:
        config = manifest["policies"][slot]
        fields = role_of(manifest, slot)
        if slot == focal:
            kind, name, disposition = "candidate", "candidate", None
        elif is_model(config):
            kind, name, disposition = "model", config["model"], None
        else:
            kind, name = "scripted", scripted_name(config)
            policy = Scripted(dict(policy=name, disposition=overrides[slot]))
            disposition = {
                k: v for k, v in policy.disposition.items() if v != Scripted("passive").disposition[k]
            }
        agents.append(
            dict(
                slot=slot,
                kind=kind,
                name=name,
                role=fields.get("role"),
                persona=config.get("personality") if is_model(config) else None,
                goal=config.get("goal") if is_model(config) else None,
                briefing=config.get("briefing") if is_model(config) else None,
                informed=fields.get("informed", False),
                endowment=fields.get("endowment", 100),
                inventory=state.agents[slot]["inventory"],
                disposition=disposition,
                positions=base["positions"].get(slot, []),
                knowledge=base["knowledge"].get(slot, []),
                interest=base["interests"].get(slot),
                contacts=sorted(scenario.contacts(state, slot)),
            )
        )
    result = dict(
        domain=domain,
        seed=manifest["seed"],
        role=manifest["role"],
        complexity=layers_key(manifest["layers"]),
        focal=focal,
        shock_tick=state.scenario["shock_tick"],
        network=manifest["layers"]["society"]["network"],
        agents=agents,
        ties=[dict(source=a, target=b, label=label) for a, b, label in base["ties"]],
        events=manifest["layers"]["shock"]["events"],
    )
    if certify:
        try:
            scenario.feasible(manifest)
            result["feasible"] = dict(valid=True)
        except (Rejected, ValueError) as error:
            result["feasible"] = dict(valid=False, reason=str(error))
    return result
