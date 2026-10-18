"""Describe failed batches without pretending that counterfactual trades actually happened.

Dependencies are inferred from requested resource flows in the same batch. They are
not a replay or a claim that an upstream action would have succeeded if corrected.
The finite credit budget avoids attributing unlimited downstream errors to one sale.
"""

from collections import Counter

AUDIT_VERSION = "action-dependencies-v1"


def number(value):
    return float(value) if type(value) in (int, float) and value > 0 else 0.0


def requested_output(action):
    p = action.get("params", {})
    if action.get("verb") == "build":
        return "project:" + str(p.get("project")), 1.0
    if action.get("verb") == "trade":
        qty = number(p.get("qty"))
        if p.get("side") == "sell":
            return "coins", qty * number(p.get("price"))
        if p.get("side") == "buy":
            return p.get("item"), qty
    return None, 0.0


def batch_audit(actions):
    credits, failures = [], []
    reserved_existing = Counter()
    for position, action in enumerate(actions):
        if action.get("status") != "rejected":
            continue
        index = action.get("index", position)
        detail = dict(action.get("error") or {"code": "invalid_action"})
        resource = detail.get("resource")
        possible = [c for c in credits if c["resource"] == resource and c["amount"] > 0]
        required, available = detail.get("required"), detail.get("available")
        missing_balance = detail["code"] == "insufficient_resource" and available is None
        effective = max(0.0, available - reserved_existing[resource]) if available is not None else None
        need = max(0.0, required - effective) if required is not None and effective is not None else 1.0
        # Missing evidence cannot establish a dependency.
        linked = possible if not missing_balance and sum(c["amount"] for c in possible) + 1e-9 >= need else []
        parents, roots = [], set()
        for credit in linked:
            parents.append(credit["index"])
            roots.update(credit["roots"])
            taken = min(need, credit["amount"])
            if not str(resource).startswith("project:"):
                credit["amount"] -= taken
            need -= taken
            if need <= 1e-9:
                break
        kind = "consequential" if parents else "unclassified" if missing_balance and possible else "initial"
        if parents and effective is not None:
            reserved_existing[resource] += min(required, effective)
        failure = dict(index=index, kind=kind, code=detail["code"], caused_by=parents,
                       root_actions=sorted(roots) if roots else [index],
                       evidence="inferred_resource_flow" if parents else "missing_resource_balance"
                       if kind == "unclassified" else "failed_precondition", detail=detail)
        failures.append(failure)
        produced, amount = requested_output(action)
        if produced and amount:
            credits.append(dict(resource=produced, amount=amount, index=index, roots=failure["root_actions"]))
    return failures


def action_audit(frames):
    counts = Counter(submitted=0, done=0, rejected=0, initial=0, consequential=0, unclassified=0)
    reasons, failures = Counter(), []
    for frame in frames:
        actions = frame.get("actions", [])
        counts["submitted"] += len(actions)
        counts["done"] += sum(a.get("status") == "done" for a in actions)
        for failure in batch_audit(actions):
            counts["rejected"] += 1
            counts[failure["kind"]] += 1
            reasons[failure["code"]] += 1
            failures.append(dict(week=frame["week"], **failure))
    return dict(version=AUDIT_VERSION, **counts, reasons=dict(reasons), failures=failures,
                dependency_basis="inferred from earlier failed actions in the same batch; not a counterfactual replay")
