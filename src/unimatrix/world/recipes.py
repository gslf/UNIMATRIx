"""Finite transformations on four materials; no dynamic code evaluation."""

from .contracts import require


def execute(steps, inputs, transforms):
    slots = dict(enumerate(inputs))
    for step in steps:
        require(step["transform_id"] in transforms, "unknown_transform")
        require(all(i in slots for i in step["input_slots"]), "unavailable_recipe_input")
        require(step["output_slot"] not in slots, "recipe_not_acyclic")
        transform = transforms[step["transform_id"]]
        require(len(step["input_slots"]) == 1, "unary_transform_required")
        slots[step["output_slot"]] = transform[slots[step["input_slots"][0]]]
    consumed = {i for step in steps for i in step["input_slots"]}
    outputs = {step["output_slot"] for step in steps} - consumed
    require(len(outputs) == 1, "ambiguous_recipe_output")
    return slots[next(iter(outputs))]
