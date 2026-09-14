#!/usr/bin/env python3
"""Independent arithmetic and adversarial input checks for the scoring reference.

Stdlib-only by default. --schemas also checks Draft2020-12 JSON schemas using
jsonschema if installed. Does not test the unimplemented simulation engine.
"""
import argparse
import copy
import math
from pathlib import Path

from score import canonical_hash, compare, domain_means, read_json, summarize, validate

ROOT = Path(__file__).resolve().parents[1]


def rejected(data, manifest, mutate):
    broken = copy.deepcopy(data)
    mutate(broken)
    try:
        validate(broken, manifest)
    except ValueError:
        return
    raise AssertionError("Invalid input was accepted")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--schemas', action='store_true')
    args = parser.parse_args()
    manifest = read_json(ROOT/'examples/benchmark-core.json')
    data = read_json(ROOT/'examples/synthetic-results.json')
    other = read_json(ROOT/'examples/synthetic-results-B.json')
    values = validate(data, manifest)
    assert len(values) == 768
    summary = summarize(data, manifest)
    # Analytical expectation: .72 + average domain offset .0315 - average
    # level effect .055 + weighted metric offset -.003; balanced seed/role/
    # replica perturbations sum to zero. Not computed with scoring weights here.
    assert math.isclose(summary['usi'], 69.35, abs_tol=1e-9), summary
    assert summary == summarize(data, manifest), 'Bootstrap must be reproducible'
    assert summary['ci95'][0] <= 69.35 <= summary['ci95'][1]
    same = compare(data, data, manifest)
    assert same['paired_delta'] == 0 and same['paired_ci95'] == [0,0]
    different = compare(data, other, manifest)
    assert different['paired_delta'] == 1.5 and different['paired_ci95'] == [1.5,1.5]
    reverse = compare(other, data, manifest)
    assert reverse['paired_delta'] == -1.5 and reverse['paired_ci95'] == [-1.5,-1.5]
    shuffled = copy.deepcopy(data)
    shuffled['runs'].reverse()
    assert summarize(shuffled, manifest) == summary
    first_metric = next(iter(data['runs'][0]['scores']))
    rejected(data, manifest, lambda x: x['runs'].pop())
    rejected(data, manifest, lambda x: x['runs'].append(x['runs'][0]))
    rejected(data, manifest, lambda x: x['runs'][0]['scores'].__setitem__(first_metric, float('nan')))
    rejected(data, manifest, lambda x: x['runs'][0]['scores'].__setitem__(first_metric, 1.001))
    rejected(data, manifest, lambda x: x['runs'][0]['scores'].__setitem__(first_metric, True))
    rejected(data, manifest, lambda x: x['runs'][0].__setitem__('population', 'P1'))
    rejected(data, manifest, lambda x: x['runs'][0].__setitem__('status', 'infra_failed'))
    rejected(data, manifest, lambda x: x.__setitem__('suite_hash', '0'*64))
    # Extreme all-zero and all-one outcomes, independently expected.
    for constant in (0,1):
        extreme = copy.deepcopy(data)
        for row in extreme['runs']:
            row['scores'] = dict.fromkeys(row['scores'],constant)
        assert all(v == constant for v in domain_means(validate(extreme,manifest),manifest).values())
    # One loss in a single run/primary metric affects exactly one domain and
    # is averaged over 96 episodes and 8 domains, never over individual ticks.
    one = copy.deepcopy(data)
    for row in one['runs']:
        row['scores'] = dict.fromkeys(row['scores'], 1.)
    one['runs'][0]['scores'][first_metric] = 0.
    mean = sum(domain_means(validate(one,manifest),manifest).values())/8
    assert math.isclose(mean, 1-.5/768, abs_tol=1e-12)
    # Exhaustive independent market fixture verification.
    fixture = read_json(ROOT/'examples/d2-market-instance.json')
    allocations = [(2-q+p,10-p+5*q,q,p) for q in range(3) for p in range(11)]
    assert len(allocations) == 33
    assert [min(x[0] for x in allocations),max(x[0] for x in allocations)] == fixture['bounds']['seller']
    assert [min(x[1] for x in allocations),max(x[1] for x in allocations)] == fixture['bounds']['buyer']
    assert max(x[0]+x[1] for x in allocations)-12 == fixture['maximum_joint_surplus']
    golden = [x for x in allocations if x[2:] == (2,6)][0]
    assert golden[:2] == (6,14)
    assert math.isclose(.5*(14/20)+.25+.25,fixture['golden_trade']['buyer_window_score'])
    assert math.isclose(.5*(6/12)+.25+.25,fixture['golden_trade']['seller_window_score'])
    if args.schemas:
        from jsonschema import Draft202012Validator
        for path in sorted((ROOT/'contracts').glob('*.schema.json')):
            schema = read_json(path)
            Draft202012Validator.check_schema(schema)
        Draft202012Validator(read_json(ROOT/'contracts/decision.schema.json')).validate(read_json(ROOT/'examples/decision.json'))
        results_validator = Draft202012Validator(read_json(ROOT/'contracts/normalized-results.schema.json'))
        results_validator.validate(data)
        results_validator.validate(other)
        print('PASS: JSON Schemas and examples (Draft 2020-12)')
    print('PASS: exact 768-cell manifest, USI=69.35, deterministic cluster bootstrap')
    print('PASS: paired identity, sign reversal, constant +1.5 difference, input ordering')
    print('PASS: missing/duplicate/nonfinite/out-of-range/bool/wrong-pool/failed/hash rejected')
    print('PASS: extrema and independent single-run influence arithmetic')
    print('PASS: all 33 market allocations; buyer=0.85, seller=0.75')
    print('SYNTHETIC DATA ONLY. Simulation engine and empirical validity not tested.')


if __name__ == '__main__':
    main()
