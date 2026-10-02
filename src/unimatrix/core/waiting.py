"""Explicit actor-authorized waiting, interrupted by observable information changes.

Waiting never advances or shortens the world's clock. An authorized inactive
barrier contains no decision row or provider call for that actor.
"""

from .ids import digest


def information_signature(packet):
    """Bind the bounded actor view, never hidden evaluator state.

    Time is handled by until_tick. Notes are the actor's own write, and receipts
    concern its previous operations (waiting must be a sole operation). Inbox
    contents always wake the actor separately; consumed messages need not match.
    All remaining view changes, including truncation and retrieval, wake it.
    """
    omitted = {k: v for k, v in packet["omitted"].items() if k not in {"inbox", "note_bytes"}}
    return digest({
        **{k: v for k, v in packet.items() if k not in {
            "tick", "protocol", "interface", "private_note", "receipts", "inbox", "omitted"}},
        "omitted": omitted,
    })


def is_waiting(state, slot, packet):
    plan = state.agents[slot].get("_wait")
    return bool(
        plan and state.agents[slot]["alive"]
        and plan["generation"] == state.agents[slot]["generation"]
        and plan["origin_tick"] < state.tick < plan["until_tick"]
        and not packet["inbox"] and not packet["omitted"].get("inbox", 0)
        and plan["information_signature"] == information_signature(packet)
    )


def decision_packets(state, packets):
    """The same eligibility rule is used by execution and provider-free replay."""
    return {slot: packet for slot, packet in packets.items() if not is_waiting(state, slot, packet)}
