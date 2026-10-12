"""Personality presets: compact trait vectors rendered into a short system prompt."""

import json
from pathlib import Path

PERSONALITIES = Path(__file__).with_name("personalities.json")
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
    for ident, persona in json.loads(PERSONALITIES.read_text(encoding="utf8")).items():
        result[ident] = dict(persona, id=ident, system_prompt=render(persona))
    return result
