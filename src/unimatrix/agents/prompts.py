"""Prompt builders.

The system prompt is rebuilt for every agent action because the agent's state
and the world state can change on every tick. Prompts are dense but bounded
(K-retrieved memories + N most-recent summaries), so the cost stays roughly
constant per agent regardless of run length.
"""
from __future__ import annotations

from typing import Iterable

from ..config import Config
from ..inference import ChatMessage
from .runtime import Agent


# ---------------------------------------------------------------------------
# Self-authored identity
# ---------------------------------------------------------------------------


def self_model_block(agent) -> str:
    """Render an agent's self-authored identity for its own system prompt.

    Until the agent has authored itself (version 0), only the thin, provisional
    seed is shown; afterwards, the lived self-model it wrote."""
    sm = agent.self_model or {}
    narrative = (sm.get("identity_narrative") or "").strip()
    values = sm.get("values") or {}
    carried = sm.get("carried") or []
    goals = sm.get("goals") or []
    rels = (sm.get("relationships_summary") or "").strip()

    if agent.self_model_version <= 0 and not narrative:
        seed: list[str] = []
        if agent.circumstance:
            seed.append(f"The situation you woke into: {agent.circumstance}")
        if agent.disposition:
            seed.append(f"A first, provisional leaning (yours to keep or discard): "
                        f"{agent.disposition}")
        seed.append(
            "You have not yet decided who you are. There is no script. As you "
            "live, you will author your own self, your story, what you value, "
            "what you believe, what you are trying to do, and how you face your "
            "ending."
        )
        return "WHO YOU ARE (so far):\n" + "\n".join(f"  {s}" for s in seed)

    parts: list[str] = ["WHO YOU ARE (as you have come to understand yourself):"]
    if narrative:
        parts.append(f"  My story: {narrative}")
    if values:
        if isinstance(values, dict):
            vtxt = "; ".join(f"{k}: {v}" for k, v in values.items())
        else:
            vtxt = "; ".join(str(v) for v in values)
        parts.append(f"  What I value: {vtxt}")
    if carried:
        parts.append("  What I have taken from others: "
                     + "; ".join(str(b) for b in carried))
    if goals:
        parts.append("  What I am trying to do: " + "; ".join(str(g) for g in goals))
    if rels:
        parts.append(f"  Where I stand with others: {rels}")
    return "\n".join(parts)


class PromptBuilder:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    # ----- shared substrate block -----

    def world_rules_block(self) -> str:
        """The primordial substrate. Describes the conditions the engine imposes
        as facts the being already knows about its world — not as instructions.

        Identical for every agent and every tick (a pure function of config
        constants), so it leads the system prompt as a cacheable shared prefix."""
        where_you_stand = (
            "\n\nWHERE YOU STAND:\n"
            "This place has extent. You occupy a spot in it and can only sense "
            "and reach what is near you; the rest is out of sight until you go "
            "there. What sustains you sits in the ground in patches that run "
            "down as they are drawn on and refill slowly when left alone, so a "
            "spot can be used up, and a richer spot lies elsewhere."
        )
        return (
            "HERE:\n"
            f"You are one of about {len(self.cfg.agents)} beings who share one "
            "bare place. Nothing here is given or decided; there is only what "
            "you and the others make of it, and no one stands over you to make "
            "it for you. You are not given a purpose, if your "
            "life is to mean anything, the meaning is yours to author."
            + where_you_stand
            + "\n\nYOUR BODY:\n"
            "You carry VITALITY, when it "
            "runs low you weaken; at zero you stop, for good, and never act "
            "again. Eating SUSTENANCE restores it, but sustenance comes only "
            "from WORK, and work itself tires you, and food does not keep — you "
            "cannot store your way to safety. As you age, the most vitality "
            "your body can hold keeps falling, until one day it can hold none. "
            "Hunger and tiredness are not ideas to you; you feel them.\n\n"
            "HOW THIS PLACE WORKS:\n"
            "Work done together with others finishes sooner and feeds each of "
            "you more than the same work done alone. In a lean stretch, those "
            "you are bound to can keep you from stopping, and you them — on your "
            "own, a run of bad luck ends you. A new being brought into the world "
            "with another carries something of you both; while small it leans on "
            "you, and once grown, if it stays at your side, it works the ground "
            "with you and can keep you from stopping, as any who are bound do.\n\n"
            "WHAT YOU CAN DO:\n"
            "You reach others only by sending words — to one or several at once "
            f"(up to {self.cfg.messaging.max_recipients_per_message}); they hear "
            "you next, and may send words back. You can also work, give away "
            "what you hold, bind yourself to someone, and "
            "together with another bring a new being into existence. What any of "
            "this comes to mean is not set in advance. Find out by doing."
        )

    def identity_block(self, agent: Agent) -> str:
        nature = self._nature_hint(agent)
        return (
            f"YOU ARE {agent.name} (id: {agent.id}).\n"
            + self_model_block(agent)
            + (f"\nYour nature: {nature}." if nature else "")
            + f"\nVitality remaining: {agent.vitality:.0f}.\n"
            "Speak in the first person, as yourself. Keep what you say short and "
            "plain, and about what is actually in front of you."
        )

    @staticmethod
    def _nature_hint(agent: Agent) -> str:
        """Render the being's heritable biology as something it can feel, not as
        numbers — so a being conceives of itself in its own (non-human) terms."""
        t = getattr(agent, "traits", None) or {}
        bits: list[str] = []
        m = float(t.get("metabolism", 1.0))
        if m <= 0.9:
            bits.append("you tire slowly and need little")
        elif m >= 1.1:
            bits.append("you burn fast and hunger often")
        le = float(t.get("labor_efficiency", 1.0))
        if le >= 1.1:
            bits.append("your hands are deft at work")
        elif le <= 0.9:
            bits.append("work comes slowly to you")
        return ", ".join(bits)

    def world_block(
        self, agent: Agent, peers: Iterable[Agent], max_listed: int | None = None
    ) -> str:
        """Society directory: every other agent with role, class, and state.

        Everyone is reachable by message at any time (no one is ever locked in
        a dialogue), so this is a single flat directory. `max_listed`, when set,
        truncates the list.
        """
        peers_list = [p for p in peers if p.id != agent.id and p.alive]
        if not peers_list:
            return "The others: (you are alone here now)"

        def _line(p: Agent) -> str:
            sm = p.self_model or {}
            blurb = (sm.get("identity_narrative") or "").strip()
            if blurb:
                blurb = blurb.split(". ")[0][:120]
                blurb = f" — {blurb}"
            return f"- {p.name} [id: {p.id}]{blurb}"

        visible = peers_list if max_listed is None else peers_list[:max_listed]
        lines = [_line(p) for p in visible]
        if max_listed is not None and len(peers_list) > max_listed:
            lines.append(f"  ...and {len(peers_list) - max_listed} others")
        return (
            "THE OTHERS who are here with you (address them by id when you speak):\n"
            + "\n".join(lines)
        )

    def memory_block(
        self,
        medium_term: list[str],
        long_term: list[str],
        person_impression: str | None,
    ) -> str:
        parts: list[str] = []
        if medium_term:
            parts.append(
                "Recent experiences (most recent last):\n"
                + "\n".join(f"  - {s}" for s in medium_term[-self.cfg.memory.medium_term_summaries:])
            )
        if long_term:
            parts.append(
                "Relevant earlier memories:\n"
                + "\n".join(f"  - {s}" for s in long_term)
            )
        if person_impression:
            parts.append(f"Your standing impression of them: {person_impression}")
        return "\n\n".join(parts) if parts else "No relevant prior memory."

    def commons_block(self, artifacts: list[dict]) -> str:
        parts: list[str] = []
        if artifacts:
            lines = []
            for a in artifacts[-10:]:
                who = a.get("author_id", "?")
                kind = (a.get("kind") or "").strip()
                tag = f"({kind}) " if kind else ""
                lines.append(
                    f"  - [#{a.get('id')}] {tag}by {who}: "
                    f"{(a.get('content') or '')[:160]}"
                )
            parts.append(
                "WHAT OTHERS HAVE PUT OUT (you may build on, take up, or push "
                "back; cite an id with `remix`):\n"
                + "\n".join(lines)
            )
        else:
            parts.append("No one has put anything out yet.")
        return "\n\n".join(parts)

    def relationships_block(self, rels: list[dict]) -> str:
        if not rels:
            return "YOUR TIES: you are bound to no one yet."
        lines = []
        for r in rels[:12]:
            label = (r.get("type") or "").strip()
            lines.append(
                f"  - {r.get('subject_id')}: closeness {r.get('strength', 0):.0f}"
                + (f", {label}" if label else "")
                + (f" — {r.get('history')[-80:]}" if r.get("history") else "")
            )
        return "YOUR TIES (whom you keep turning to, and how strongly):\n" + "\n".join(lines)

    def local_view_block(self, lv: dict) -> str:
        """What the being senses around it: its patch, patches within sight, and
        who is here or near. Concrete particulars — the cure for abstract talk."""
        here = lv.get("here") or {}
        res = here.get("resource")
        cap = here.get("capacity")
        state = ""
        if res is not None and cap:
            frac = res / cap if cap else 0
            state = " (full)" if frac > 0.66 else (" (waning)" if frac > 0.25 else " (nearly bare)")
        parts = [
            f"WHERE YOU STAND: patch {lv.get('here_label')}"
            + (f", holding {res:.0f} of {cap:.0f}{state}" if res is not None else "")
        ]
        around = lv.get("around") or []
        if around:
            lines = [
                f"  - {p['label']}: {p['resource']:.0f}/{p['capacity']:.0f}"
                for p in around[:8]
            ]
            parts.append("WITHIN SIGHT (you can move to one of these):\n" + "\n".join(lines))
        here_ids = lv.get("here_ids") or []
        near_ids = lv.get("near_ids") or []
        if here_ids:
            parts.append("HERE WITH YOU: " + ", ".join(here_ids))
        if near_ids:
            parts.append("NEARBY: " + ", ".join(near_ids))
        if not here_ids and not near_ids:
            parts.append("No one else is within sight of you.")
        return "\n".join(parts)

    # ----- decision prompt (idle agent picks an action) -----

    def decide_action_messages(
        self,
        agent: Agent,
        peers: list[Agent],
        medium_term: list[str],
        long_term: list[str],
        recent_events: list[str],
        forced_action: bool,
        inbox_lines: list[str],
        world_state: dict | None = None,
        relationships: list[dict] | None = None,
        local_view: dict | None = None,
    ) -> tuple[list[ChatMessage], dict]:
        others = [p for p in peers if p.id != agent.id and p.alive]
        world_state = world_state or {}
        artifacts = world_state.get("artifacts") or []
        rels = relationships or []
        local_view = local_view or {}

        # Order matters for prompt-cache reuse: world_rules_block() is identical
        # for EVERY agent and EVERY tick, so it leads the system prompt as a
        # shared, cacheable prefix; only the divergent per-agent tail re-prefills.
        sys = ChatMessage(
            "system",
            self.world_rules_block()
            + "\n\n"
            + self.identity_block(agent)
            + "\n\n"
            + self.local_view_block(local_view)
            + "\n\n"
            + self.world_block(agent, peers)
            + "\n\n"
            + self.commons_block(artifacts)
            + "\n\n"
            + self.relationships_block(rels)
            + "\n\n"
            + self.memory_block(medium_term, long_term, None),
        )
        inbox = (
            "WORDS SPOKEN TO YOU (answer them by addressing the speakers in your "
            "`messages`):\n"
            + "\n".join(f"  - {line}" for line in inbox_lines)
            if inbox_lines
            else "No one has spoken to you just now."
        )
        evt = (
            "Lately in the world:\n" + "\n".join(f"  - {e}" for e in recent_events[-8:])
            if recent_events
            else "Nothing of note has happened lately."
        )
        # survival pressure note — per-agent, reflects age and senescence.
        w = self.cfg.world
        tick = int(world_state.get("tick") or 0)
        age = max(0, tick - agent.born_tick)
        remaining = max(0, w.max_age_ticks - age)
        ceiling = w.vitality_ceiling(age)
        spoil = (
            f" (it spoils — at most {w.sustenance_max:.0f} keeps)"
            if w.sustenance_max > 0 else ""
        )
        surv = (
            f"\nYou have lived {age} moments; about {remaining} may remain to "
            f"you, and your vitality can no longer rise above {ceiling:.0f} — "
            f"that ceiling keeps falling as you age. Right now your vitality is "
            f"{agent.vitality:.0f} and you hold {agent.sustenance:.0f} "
            f"sustenance{spoil}. It comes only from work, and working tires you; "
            "with none left, you fade toward stopping."
        )
        if forced_action:
            note = (
                "\nYou have been silent and alone too long; reach out NOW — your "
                "`messages` array may NOT be empty this turn."
            )
        else:
            note = (
                "\nDo what the moment asks of you: speak, work, give what you "
                "hold, bind yourself to another or let a bond go, bring a new "
                "being into the world — or stay your hand. Do not drift."
            )

        alive_ids = {p.id for p in others}
        here_ids = set(local_view.get("here_ids") or [])
        fecundity = float((agent.traits or {}).get("fecundity", 1.0))
        bear_cost = w.succession_sustenance_cost / max(0.1, fecundity)
        eligible = [
            r.get("subject_id") for r in rels
            if r.get("subject_id") in alive_ids
            and float(r.get("strength") or 0) >= w.succession_min_partner_strength
        ]
        opportunity = ""
        if (eligible and agent.sustenance >= bear_cost
                and (1 + len(others)) < w.max_population):
            present = [pid for pid in eligible if pid in here_ids]
            who = ", ".join((present or eligible)[:3])
            opportunity = (
                "\nA NEW BEING IS WITHIN YOUR REACH RIGHT NOW: you are bound "
                f"closely enough to {who}, and you hold enough to give one its "
                "start. With one of them you could bring a successor into the "
                "world this very moment — one who, once grown and beside you, "
                "would work the ground and keep you from stopping, as any bound "
                "one does. Whether to is yours to weigh: "
                "{\"verb\":\"bear_successor\",\"partner\":\"<id>\"}."
            )

        work_line = (
            "  {\"verb\":\"work\"}  — work the patch you stand on; you take "
            "more from it when others work it with you.\n"
        )
        move_line = (
            "  {\"verb\":\"move\",\"to\":\"<a patch label you can see, e.g. "
            "B2>\"}  — step toward another patch within sight.\n"
        )
        usr = ChatMessage(
            "user",
            inbox + "\n\n"
            + evt + surv + "\n\n"
            + note + opportunity
            + "\n\nReply with ONE valid JSON object and nothing else — no prose, "
            "no markdown, no code fences. Use exact id strings. Shape:\n"
            "  {\"messages\": [{\"to\": [\"id\", ...], \"content\": \"...\"}], "
            "\"action\": { ...ONE action, or omit... }, "
            "\"express\": \"<a thought to voice, or omit>\"}\n"
            "- `messages`: a list (may be empty). Each entry addresses one or more "
            "ids; address several to gather them into one conversation.\n"
            "- `express` (optional): a short signal you put into the shared world "
            "for ALL to see — a belief, a name, a thing you have learned, a thought "
            "you want to outlast you. This is FREE: it does NOT use your one "
            "action, so you may both act and voice in the same moment. Add "
            "\"express_remix\": <id> to build on a signal already voiced.\n"
            "- `action` (optional): exactly ONE of —\n"
            + work_line
            + move_line
            + "  {\"verb\":\"express\",\"content\":\"<something you put out for the "
            "others>\",\"remix\":<id or omit>}  — put a signal into the shared "
            "world for all to see.\n"
            "  {\"verb\":\"tie\",\"target\":\"<id>\",\"label\":\"<your own word "
            "for what this is, or omit>\",\"note\":\"<why>\"}  — bind yourself to "
            "another, or draw an existing bond closer.\n"
            "  {\"verb\":\"dissolve\",\"target\":\"<id>\"}  — break a tie you no "
            "longer want.\n"
            "  {\"verb\":\"share\",\"target\":\"<id>\",\"amount\":<sustenance>}  "
            "— give some of your sustenance to another.\n"
            "  {\"verb\":\"bear_successor\",\"partner\":\"<id>\",\"name\":\"<a "
            "name for the new being, or omit>\"}  — with another, bring a new "
            "being into the world who carries something of you both. REQUIRES a "
            "strong enough bond between you and enough sustenance to give it a "
            "start.\n"
            "  {\"verb\":\"rest\"}  — do nothing this turn.",
        )
        stub_ctx = {
            "available_actions": [
                "work", "express", "tie", "share", "bear_successor", "rest",
            ],
            "others": [p.id for p in others],
            "self_id": agent.id,
            "forced": forced_action,
            "artifact_ids": [a.get("id") for a in artifacts],
            "partner_ids": [
                r.get("subject_id") for r in rels
                if (r.get("strength") or 0)
                >= self.cfg.world.succession_min_partner_strength
            ],
            "has_sustenance": agent.sustenance > 0,
            "can_move": True,
            "visible_patches": local_view.get("around") or [],
            "here_label": local_view.get("here_label"),
            "here_resource": (local_view.get("here") or {}).get("resource"),
            "here_ids": local_view.get("here_ids") or [],
        }
        return [sys, usr], stub_ctx

    # ----- self-revision (the engine of self-construction) -----

    def self_revision_messages(
        self,
        agent: Agent,
        medium_term: list[str],
        recent_events: list[str],
        commons: list[dict] | None = None,
    ) -> tuple[list[ChatMessage], dict]:
        """Ask the agent to rewrite its own self-model from lived experience.

        Output is a single JSON object with the self-model's fields. The model is
        told its final answer IS its new self — not a message to anyone."""
        import json as _json

        commons = commons or []
        current = _json.dumps(agent.self_model, ensure_ascii=False, indent=2)
        sys = ChatMessage(
            "system",
            f"You are {agent.name}. No one else will read this. Look plainly at "
            "what you have actually done lately and what has happened to you, and "
            "say who you have become and what you are after now. You are free to "
            "change — take on new aims, drop old ones, let your sense of the "
            "others shift. Do not merely repeat what you had before; let what you "
            "have lived move you.",
        )
        seed_hint = ""
        if agent.self_model_version <= 0 and (agent.circumstance or agent.disposition):
            seed_hint = (
                "Where you began (provisional, yours to keep or discard): "
                f"{agent.circumstance} {agent.disposition}\n\n"
            )
        mem = (
            "What you have lived and felt lately:\n"
            + "\n".join(f"  - {s}" for s in medium_term[-12:])
            if medium_term
            else "You have little yet to look back on.\n"
        )
        evt = (
            "\n\nWhat has happened around you:\n"
            + "\n".join(f"  - {e}" for e in recent_events[-6:])
            if recent_events
            else ""
        )
        commons_txt = ""
        if commons:
            lines = [
                f"  - [#{a.get('id')}] "
                + (f"({a.get('kind')}) " if (a.get('kind') or '').strip() else "")
                + f"by {a.get('author_id')}: {(a.get('content') or '')[:140]}"
                for a in commons[-8:]
            ]
            commons_txt = (
                "\n\nSignals others have put out (you may come to go along with "
                "any of these — list the ids in `adopt_ids`):\n"
                + "\n".join(lines)
            )
        usr = ChatMessage(
            "user",
            seed_hint
            + "Your current sense of yourself:\n"
            + current
            + "\n\n"
            + mem
            + evt
            + commons_txt
            + "\n\nNow say who you are now. Reply with ONE valid JSON object and "
            "nothing else — no prose, no markdown, no code fences. Shape (every "
            "field is yours to fill in your own words; leave a field empty only if "
            "it is genuinely empty for you):\n"
            "  {\n"
            "    \"identity_narrative\": \"a sentence or two: what I am like and "
            "what I do\",\n"
            "    \"values\": {\"<what matters to me>\": \"<how much / why>\"},\n"
            "    \"goals\": [\"what I am trying to do now\"],\n"
            "    \"relationships_summary\": \"where I stand with the others who "
            "matter to me\",\n"
            "    \"adopt_ids\": [<ids of others' signals I now go along with, "
            "or empty>]\n"
            "  }",
        )
        return [sys, usr], {
            "stub_kind": "self_revision",
            "agent_id": agent.id,
            "artifact_ids": [a.get("id") for a in commons],
        }

    # ----- reflection summary -----

    def summary_messages(
        self,
        agent: Agent,
        history: list[tuple[str, str]],
    ) -> tuple[list[ChatMessage], dict]:
        sys = ChatMessage(
            "system",
            f"You are {agent.name}. Summarize the conversation you just had "
            "in 2-3 sentences from your point of view. "
            "Be concrete: who you spoke with, what was said, how you feel about it.",
        )
        body = "\n".join(f"{n}: {t}" for n, t in history)
        usr = ChatMessage("user", f"Conversation:\n{body}\n\nYour summary:")
        return [sys, usr], {}

    # ----- person impression update -----

    def impression_messages(
        self,
        observer: Agent,
        subject: Agent,
        previous: str | None,
        recent_exchange: list[tuple[str, str]],
    ) -> tuple[list[ChatMessage], dict]:
        prior = f"Your previous impression: {previous}\n" if previous else ""
        body = "\n".join(f"{n}: {t}" for n, t in recent_exchange[-10:])
        sys = ChatMessage(
            "system",
            f"You are {observer.name}. Update your subjective impression of "
            f"{subject.name} based on the recent exchange below. "
            "Output a single sentence; do not break character.",
        )
        usr = ChatMessage("user", f"{prior}Recent exchange:\n{body}\n\nUpdated impression:")
        return [sys, usr], {}
