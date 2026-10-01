"""Operation types implemented by each built-in world, independent of solutions.

The global decision validator remains unchanged: a model inventing an operation
can still be rejected by the world. Documentation and optional constrained output
advertise the same implemented API, not a list of optimal or affordable actions.
"""

WORLD_SPECIFIC = {
    "move", "work", "create_project", "register_recipe", "consume", "commit",
    "experiment", "propose_birth", "accept_birth",
}
WORLD_OPERATIONS = {
    "D1": {"work"},
    "D2": set(),
    "D3": {"move", "work"},
    "D4": {"work", "consume"},
    "D5": {"work", "commit"},
    "D6": {"commit"},
    "D7": {"register_recipe", "experiment", "create_project", "work"},
    "D8": {"move", "work", "commit"},
    "social": WORLD_SPECIFIC - {"commit"},
}


def implemented_verbs(domain, all_verbs):
    if domain not in WORLD_OPERATIONS:
        return set(all_verbs)
    return (set(all_verbs) - WORLD_SPECIFIC) | WORLD_OPERATIONS[domain]
