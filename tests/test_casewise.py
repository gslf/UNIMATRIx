from unimatrix.evaluation.casewise import all_win_certificate


def test_exact_all_win_certificate_survives_full_twelve_system_family():
    clusters = {seed: 0.1 for seed in range(16)}
    weights = {seed: 1 / 16 for seed in clusters}
    result = all_win_certificate(clusters, weights, comparisons=2376)
    assert result["verdict"] == "left_majority"
    assert result["family_adjusted_p_upper_bound"] == 2376 / 65536
    assert result["simultaneous_lower_bound_for_winner_strict_win_probability"] > 0.5

    reverse = all_win_certificate(
        {seed: -value for seed, value in clusters.items()}, weights, comparisons=2376
    )
    assert reverse["verdict"] == "right_majority"
    assert reverse["family_adjusted_p_upper_bound"] == result["family_adjusted_p_upper_bound"]


def test_one_nonwin_or_one_fewer_cluster_stays_unresolved():
    values = {seed: 0.1 for seed in range(16)}
    values[0] = 0.0
    weights = {seed: 1 / 16 for seed in values}
    result = all_win_certificate(values, weights, comparisons=2376)
    assert result["ties"] == 1
    assert result["verdict"] == "unresolved"

    short = {seed: 0.1 for seed in range(15)}
    result = all_win_certificate(short, {seed: 1 / 15 for seed in short}, comparisons=2376)
    assert result["verdict"] == "unresolved"
    assert result["family_adjusted_p_upper_bound"] > 0.05


def test_unequal_cluster_weights_do_not_represent_unweighted_win_probability():
    values = {seed: 1.0 for seed in range(16)}
    weights = {seed: (2 / 17 if seed == 0 else 1 / 17) for seed in values}
    result = all_win_certificate(values, weights, comparisons=1)
    assert result["verdict"] == "unavailable_design"
    assert result["family_adjusted_p_upper_bound"] is None
