"""Reproduce frozen Standard v2.1 rates without reading model runs or calling a provider.

python tests/tools/calibrate_standard_v2_1.py --output docs/standard-v2.1-calibration.json
"""

import argparse
import json
from statistics import fmean

from unimatrix.core.ids import digest
from unimatrix.polis.recipe import PolisRecipes
from unimatrix.polis.simulate import calibrate, suggested_rates


def calibration():
    recipe = PolisRecipes().get("standard-v2.1")
    rows = calibrate(recipe, [1, 2, 3, 4, 5], ("expert", "oracle"))
    suggested = suggested_rates(rows, recipe)
    validation = calibrate(recipe, [6, 7, 8, 9, 10], ("expert", "oracle"))
    return dict(recipe_hash=digest(recipe), target_points=1000, calibration_seeds=[1, 2, 3, 4, 5],
                validation_seeds=[6, 7, 8, 9, 10], rates=suggested,
                matches_frozen_rates=suggested == recipe["scoring"]["rates"],
                construction_service_potential=recipe["ticks"] * sum(p["value"] for p in recipe["projects"].values()),
                method="1000 / mean expert natural units; D3 additionally bounded by 1000 / full-horizon service potential. "
                       "Fixed exchange rates, no score cap. No model runs used.",
                calibration=rows, validation=validation,
                validation_means={p: {d: round(fmean(r["domains"][d] for r in validation if r["policy"] == p), 3)
                                      for d in suggested} for p in ("expert", "oracle")})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=argparse.FileType("w", encoding="utf8"), required=True)
    args = parser.parse_args()
    result = calibration()
    json.dump(result, args.output, ensure_ascii=False, indent=2)
    args.output.write("\n")
    args.output.close()
    print(json.dumps({k: result[k] for k in ("matches_frozen_rates", "rates", "validation_means")}))
