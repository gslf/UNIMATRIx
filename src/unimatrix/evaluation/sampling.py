"""Design-only sampling units for matched evaluation panels.

Shared seeds are whole-world blocks. Independent-cell seeds are resampled only
within their fixed population/domain/condition/role stratum. Replicates never
become independent units. Partially shared seed designs require a separate design.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class SamplingLayout:
    kind: str
    pools: dict[tuple, list[int]]

    @property
    def minimum_clusters(self):
        return min(map(len, self.pools.values()))

    def complete_prefixes(self, order):
        """Preserve every stratum's frozen proportion at each eligible prefix."""
        ownership = {seed: pool for pool, seeds in self.pools.items() for seed in seeds}
        if len(set(order)) != len(order) or set(order) != set(ownership):
            raise ValueError("seed_order_must_cover_frozen_reference")
        counts = Counter()
        first = next(iter(self.pools))
        for size, seed in enumerate(order, 1):
            counts[ownership[seed]] += 1
            if all(counts[p] * len(self.pools[first]) == counts[first] * len(seeds)
                   for p, seeds in self.pools.items()):
                yield size


def sampling_layout(keys, population_by_seed):
    """Classify a panel from design keys, without inspecting any model outcomes."""
    keys = set(keys)
    if not keys:
        raise ValueError("nonempty_sampling_design_required")
    shapes = defaultdict(set)
    for domain, condition, seed, role, replicate in keys:
        shapes[seed].add((domain, condition, role, replicate))
    seeds = sorted(shapes)
    pools = defaultdict(list)
    first = shapes[seeds[0]]
    if all(shape == first for shape in shapes.values()):
        for seed in seeds:
            pools[(population_by_seed[str(seed)],)].append(seed)
        return SamplingLayout("shared_seed_blocks", dict(sorted(pools.items())))

    replicates = {k[4] for k in keys}
    cells = {(k[0], k[1], k[3]) for k in keys}
    populations = {population_by_seed[str(s)] for s in seeds}
    for seed in seeds:
        shape = shapes[seed]
        cell = next(iter(shape))[:3]
        if shape != {(*cell, rep) for rep in replicates}:
            raise ValueError("balanced_shared_or_independent_cell_sampling_required")
        pools[(population_by_seed[str(seed)], *cell)].append(seed)
    for population in populations:
        counts = [len(pools.get((population, *cell), [])) for cell in cells]
        if not counts or min(counts) == 0 or len(set(counts)) != 1:
            raise ValueError("balanced_independent_cell_strata_required")
    return SamplingLayout("independent_cell_strata", dict(sorted(pools.items())))
