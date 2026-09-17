"""Public recipe terminology over the stable execution format and identities."""

import re
from pathlib import Path

from .plans import (
    BUNDLED,
    DEFAULT_PLAN,
    PlanRepository,
    bind_candidate,
    scoring_spec,
    validate_plan,
)

DEFAULT_RECIPE = DEFAULT_PLAN
validate_recipe = validate_plan


def recipe_text(value):
    """Translate legacy display copy without changing saved JSON or its hashes."""
    value = re.sub(r"\bcampaign(s?)\b", lambda m: (
        "Evaluation" if m[0][0].isupper() else "evaluation"
    ) + m[1], value, flags=re.IGNORECASE)
    return re.sub(
        r"\b(?:benchmark )?plans?\b",
        lambda m: (
            ("Benchmark recipe" if m[0][0].isupper() else "benchmark recipe")
            + ("s" if m[0].endswith("s") else "")
        ),
        value,
        flags=re.IGNORECASE,
    )


def recipe_fields(record):
    """Expose current API names while retaining legacy fields for existing clients."""
    result = dict(record)
    for key in ("plan", "plans", "plan_id", "plan_name", "plan_hash", "base_plan"):
        if key in result:
            result[key.replace("plan", "recipe")] = result[key]
    if "description" in result:
        result["description"] = recipe_text(result["description"])
    if "notes" in result:
        result["notes"] = [recipe_text(note) for note in result["notes"]]
    if result.get("error"):
        result["error"] = recipe_text(result["error"])
    if "studies" in result:
        result["studies"] = [recipe_fields(study) for study in result["studies"]]
    if "design" in result:
        result["design"] = recipe_fields(result["design"])
    if result.get("analysis"):
        result["analysis"] = recipe_fields(result["analysis"])
    return result


class RecipeRepository(PlanRepository):
    def __init__(self, directory="config/recipes", default=DEFAULT_RECIPE):
        # Existing installations keep discovering their authored files without moving them.
        if directory is not None and Path(directory) == Path("config/recipes"):
            legacy = Path("config/plans")
            if not Path(directory).exists() and legacy.is_dir():
                directory = legacy
        super().__init__(directory, default)


__all__ = [
    "BUNDLED",
    "DEFAULT_RECIPE",
    "RecipeRepository",
    "bind_candidate",
    "scoring_spec",
    "validate_recipe",
    "recipe_fields",
    "recipe_text",
]
