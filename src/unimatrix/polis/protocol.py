"""Shared action vocabulary and contract for every village."""

def canonical_variant(value):
    if not isinstance(value, str):
        return value
    token = value.strip().lower().replace("-", "_").replace(" ", "_")
    return {"sealed_bids": "sealed_bid", "sealed_bid": "sealed_bid", "haggling": "haggle"}.get(token, token)


def variants(recipe, domain):
    """Vocabulary, not the currently required (potentially hidden) convention."""
    values = {recipe["world"]["conventions"].get(domain)}
    for node in recipe["story"]["nodes"]:
        for effect in node["effects"]:
            if effect["type"] == "convention_shift" and effect["domain"] == domain:
                values.add(effect["variant"])
            if domain == "trade" and effect["type"] == "merchant" and effect["field"] == "protocol":
                values.add(effect["value"])
    if domain == "trade":
        values.update(m["protocol"] for m in recipe["merchants"])
    return sorted({canonical_variant(v) for v in values if v is not None})
