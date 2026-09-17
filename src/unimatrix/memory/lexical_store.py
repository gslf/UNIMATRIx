"""Frozen lexical tokenizer; experience records live in canonical snapshots."""

import re


def tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))
