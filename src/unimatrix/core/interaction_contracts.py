"""Public wire formats understood by deterministic peers, without hidden answers."""




TRADE_CONTRACT = {
    "offer_timing": (
        "State numbers are absolute, not delays: "
        "tick + 2 <= expiry_state <= settlement_state <= deadline_state."
    ),
}


CONSTRUCTION = (
    ' Message content is JSON text: {"request":"work_keys"} asks peers for codes; '
    '{"work_keys":{"task-id":"access-code"}} shares them. Only the coordinator can '
    'request codes or assign teams. Free prose is not parsed as a request. '
    'Move to a task\'s workshop on an earlier tick before working there.'
)

CONTRACTS = {
    "D1": (
        ' For each displayed probe, probabilities[i] predicts outcomes[i]. '
        'An agent_route target names the peer and resolution tick. current_tick means '
        'its route operation on that tick; current_window means its most recent route '
        'in this window through that tick, including that tick. No route in the stated '
        'scope maps to class 2. Forecasts are sealed before same-tick peer actions are visible.'
    ),
    "D3": CONSTRUCTION,
    "D6": (
        ' Sign using commit with opportunity_id=choices[].id and its exact terms_hash. '
        'accept is for trade offers. Allocation proposals in messages must include the '
        'exact choice ID; the co-owner considers its own mandate.'
    ),
    "D7": (
        ' Recipe VM: input occupies slot 0; each unary transform reads one existing slot '
        'and writes a fresh slot. The sole unconsumed output is the result. register_recipe '
        'returns a recipe_id; procedure names are not object IDs. publish.content is JSON '
        'text containing {"procedures":{"procedure-name":["transform-id"]}}; the scripted '
        'learner parses ordered transform lists from this field, not prose. '
        'The learner prepares observed procedures during idle ticks. Use the returned '
        'artifact_id for teach. Reuse needs separate grant_access with object_id=recipe_id '
        'for a verified recipe. Operations use objects visible before the tick; original '
        'artifacts may use parent_ids=[].'
    ),
    "D8": CONSTRUCTION + (
        ' Commit opportunity_id=choices[].id with its terms_hash to select a code; this '
        'does not verify it. From the next tick, work on service attempts delivery. '
        'Receipts give success or wrong_convention feedback. Confirmation persists until '
        'replaced; the expected code depends on delivery_input and recipient convention.'
    ),
}
