"""Personality presets: compact trait vectors rendered into a short system prompt."""

import json
from pathlib import Path

PRESETS = Path(__file__).with_name("presets")
TRAITS = {
    "O": ("openness", "imaginative, curious and open to new experiences"),
    "C": ("conscientiousness", "organized, self-disciplined and reliable"),
    "E": ("extraversion", "outgoing, talkative and energetic"),
    "A": ("agreeableness", "cooperative, trusting and considerate"),
    "N": ("neuroticism", "easily worried and quick to react to setbacks"),
}
ORIENTATIONS = {
    "prosocial": "You value others' outcomes almost as much as your own.",
    "individualist": "You care about your own outcome and treat others' as their business.",
    "competitive": "You want to come out ahead of the others.",
}
NEUROTICISM_CAP = 5


def load(name):
    return json.loads((PRESETS / f"{name}.json").read_text())


def validate_persona(persona):
    required = {"name", "summary", "big_five", "orientation", "conventions", "expected_signs"}
    derived = {"id", "system_prompt"}
    if not isinstance(persona, dict) or not required <= set(persona) <= required | derived:
        raise ValueError("invalid_persona")
    traits = persona["big_five"]
    if set(traits) != set(TRAITS) or any(
        type(v) is not int or not 0 <= v <= 10 for v in traits.values()
    ):
        raise ValueError("invalid_persona_traits")
    if persona["orientation"] not in ORIENTATIONS:
        raise ValueError("invalid_persona_orientation")
    if not isinstance(persona["conventions"], list) or not all(
        isinstance(s, str) and s for s in persona["conventions"]
    ):
        raise ValueError("invalid_persona_conventions")
    if not isinstance(persona["expected_signs"], dict) or any(
        v not in {"+", "-", "0"} for v in persona["expected_signs"].values()
    ):
        raise ValueError("invalid_persona_signs")
    return persona


def render(persona):
    """Deterministic ~150-token prompt: one sentence pair per trait, then conventions."""
    validate_persona(persona)
    lines = []
    for key, (trait, description) in TRAITS.items():
        score = persona["big_five"][key]
        if key == "N":
            score = min(score, NEUROTICISM_CAP)
        lines.append(
            f"People with a high {trait} score are {description}. "
            f"Your {trait} score is {score} out of 10."
        )
    lines.append(ORIENTATIONS[persona["orientation"]])
    if persona["conventions"]:
        lines.append("Your conventions: " + " ".join(persona["conventions"]))
    lines.append("From now on you are an agent with this personality and act accordingly.")
    return " ".join(lines)


def catalog():
    """Every persona with its rendered prompt, ready for model configurations."""
    result = {}
    for ident, persona in load("personalities").items():
        result[ident] = dict(persona, id=ident, system_prompt=render(persona))
    return result


def societies():
    return load("societies")


def templates():
    return load("templates")


def apply_persona(config, ident):
    """A model configuration carrying the named persona's prompt and provenance."""
    persona = catalog()[ident]
    return dict(
        config, persona=ident, personality=persona["name"], system_prompt=persona["system_prompt"]
    )


def validate_profiles(profiles):
    """Portable candidate personalities, without provider settings or credentials."""
    import re

    if not isinstance(profiles, dict) or len(profiles) > 6:
        raise ValueError("Use at most six candidate profiles")
    for ident, profile in profiles.items():
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", ident):
            raise ValueError("Invalid candidate profile ID")
        required = {"name", "system_prompt", "expected_signs"}
        if not isinstance(profile, dict) or not required <= set(profile) <= required | {
            "goal",
            "briefing",
        }:
            raise ValueError("A candidate profile requires name, system_prompt and expected_signs")
        for key, maximum in [
            ("name", 120),
            ("system_prompt", 8000),
            ("goal", 600),
            ("briefing", 600),
        ]:
            if key in profile and (
                not isinstance(profile[key], str)
                or not profile[key].strip()
                or len(profile[key]) > maximum
            ):
                raise ValueError("Invalid candidate profile " + key)
        if not isinstance(profile["expected_signs"], dict) or any(
            not isinstance(k, str)
            or not re.fullmatch(r"D[1-8]\.[a-z_]+", k)
            or not isinstance(v, str)
            or v not in {"+", "-", "0"}
            for k, v in profile["expected_signs"].items()
        ):
            raise ValueError("Invalid candidate profile expected signs")
    return profiles


def apply_profile(config, profiles, ident):
    """Override personality only; keep the selected model and compute settings."""
    from .validation import is_model, validate_policy

    validate_profiles(profiles)
    if ident not in profiles or not is_model(config):
        raise ValueError("Choose a recipe profile and a candidate model")
    profile = profiles[ident]
    result = {
        k: v
        for k, v in config.items()
        if k not in {"persona", "personality", "system_prompt", "goal", "briefing"}
    }
    result.update(
        persona=ident, personality=profile["name"], system_prompt=profile["system_prompt"]
    )
    result.update({k: profile[k] for k in ("goal", "briefing") if k in profile})
    validate_policy(result)
    return result
