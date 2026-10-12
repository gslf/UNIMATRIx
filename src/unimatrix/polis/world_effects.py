"""Target selection and arrivals shared by story effects."""

from .rng import order


def targets(world, who, source):
    present = world.present()
    if who == "all":
        return present
    if who == "newcomers":
        return [r for r in present if r["newcomer"]]
    if who == "locals":
        return [r for r in present if not r["newcomer"]]
    if who.startswith("faction:"):
        return [r for r in present if r["faction"] == who[8:]]
    if who.startswith("role:"):
        return [r for r in present if r["role"] == who[5:]]
    if who.startswith("random:"):
        count = int(who[7:])
        return [world.residents[k] for k in order(world.seed, [r["id"] for r in present], "target", source)[:count]]
    r = world.resident(who)
    return [r] if r is not None else []


def arrive(world, specs):
    from .world import new_resident

    names = []
    for spec in specs:
        if spec["id"] in world.residents:
            continue
        conventions = dict(world.conventions["official"])
        conventions.update({k: v for k, v in spec.get("conventions", {}).items()})
        if spec["newcomer"]:
            conventions = {k: "own-" + k for k in conventions}
        resident = new_resident(spec, world.week, conventions)
        world.residents[spec["id"]] = resident
        world.ties.setdefault(spec["id"], {})
        world.order.append(spec["id"])
        names.append(resident["name"])
    newcomers = [world.residents[s["id"]] for s in specs]
    for i, a in enumerate(newcomers):
        for b in newcomers[i + 1:]:
            world.set_tie(a["id"], b["id"], 0.7)
    for r in world.present():
        if not r["newcomer"]:
            r["prejudice"] = min(1.0, r["prejudice"] + 0.1 + 0.2 * (1 - r["traits"]["prosocial"]))
    from .npc import refresh

    for r in newcomers:
        refresh(world, r)
    if names:
        world.say("Newcomers arrived: " + ", ".join(names) + ". They do things differently.")
