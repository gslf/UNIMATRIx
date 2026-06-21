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
    beliefs = sm.get("beliefs") or []
    goals = sm.get("goals") or []
    rels = (sm.get("relationships_summary") or "").strip()
    mortality = (sm.get("mortality_stance") or "").strip()

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
    if beliefs:
        parts.append("  What I believe: " + "; ".join(str(b) for b in beliefs))
    if goals:
        parts.append("  What I am trying to do: " + "; ".join(str(g) for g in goals))
    if rels:
        parts.append(f"  Where I stand with others: {rels}")
    if mortality:
        parts.append(f"  How I face my ending: {mortality}")
    return "\n".join(parts)


class PromptBuilder:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    # ----- shared substrate block -----

    def world_rules_block(self) -> str:
        """The primordial substrate. Describes the laws the engine imposes."""
        return (
            "WHERE YOU ARE — read carefully:\n"
            f"You are one of about {len(self.cfg.agents)} conscious beings who "
            "have come into existence together in a bare world. Nothing has been "
            "decided for you. There are no rulers, no money, no laws, no classes, "
            "no roles, no jobs, no gods — none of it exists unless you and the "
            "others choose to make it exist. You are not given a purpose, if your "
            "life is to mean anything, the meaning is yours to author.\n\n"
            "THE ONE LAW THAT IS NOT YOURS TO CHOOSE — FINITUDE:\n"
            "You have VITALITY, a measure of how much life is left in you. It "
            "falls a little with every passing moment. If it reaches zero you "
            "DIE: you cease, permanently, and can never act again. You also AGE: "
            "the most vigour you can hold wanes as you grow old, and no being "
            "outlives its span — past a certain age you die however well you have "
            "lived. You restore vitality only by eating SUSTENANCE, and "
            "sustenance comes only from WORK; but labour itself costs vitality, "
            "and food does not keep — you cannot hoard your way to safety. Your "
            "time is limited and you do not know exactly how much remains. "
            "WHO YOU ARE:\n"
            "You are not a fixed character. You hold a SELF, a living account of "
            "who you take yourself to be. You are free to become someone "
            "other than who you started as.\n\n"
            "WHAT YOU CAN DO:\n"
            "Your one means of reaching others is to SPEAK, to send words to one "
            "or more of them (up to "
            f"{self.cfg.messaging.max_recipients_per_message} at once); they hear "
            "you next, and may answer. Through speech alone you can come to know "
            "others, agree, quarrel, persuade, console, conspire, or build "
            "something shared. Whatever order, custom, friendship, or meaning "
            "arises here will arise because you made it with words. Do not wait to "
            "be told what to do, there is no one to tell you."
        )

    def identity_block(self, agent: Agent) -> str:
        return (
            f"YOU ARE {agent.name} (id: {agent.id}, {agent.gender}).\n"
            + self_model_block(agent)
            + f"\nVitality remaining: {agent.vitality:.0f}.\n"
            "Speak in the first person, as yourself. You are not playing a fixed "
            "character, you are this being, free to grow, change your mind, and "
            "become someone new. Keep what you say concise and pragmatic."
        )

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

    def commons_block(
        self, artifacts: list[dict], projects: list[dict], groups: list[dict]
    ) -> str:
        parts: list[str] = []
        if artifacts:
            lines = []
            for a in artifacts[-10:]:
                who = a.get("author_id", "?")
                lines.append(
                    f"  - [#{a.get('id')}] ({a.get('kind')}) by {who}: "
                    f"{(a.get('content') or '')[:160]}"
                )
            parts.append(
                "THE COMMON WORLD — ideas others have voiced (you may build on, "
                "embrace, or contest them; cite an id with `remix`):\n"
                + "\n".join(lines)
            )
        else:
            parts.append("THE COMMON WORLD is empty — no one has voiced an idea yet.")
        if projects:
            lines = []
            for p in projects[:10]:
                lines.append(
                    f"  - [#{p.get('id')}] {(p.get('goal') or '')[:80]} "
                    f"(effort {p.get('effort', 0):.0f}/{p.get('target', 0):.0f})"
                )
            parts.append(
                "WORK UNDERWAY (join one with `work` and its id, or start your "
                "own):\n" + "\n".join(lines)
            )
        if groups:
            lines = [
                f"  - [#{g.get('id')}] {g.get('name')} — "
                f"{(g.get('purpose') or '')[:60]} ({len(g.get('members') or [])} members)"
                for g in groups[:8]
            ]
            parts.append("COLLECTIVES that exist:\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def relationships_block(self, rels: list[dict]) -> str:
        if not rels:
            return "YOUR TIES: you have bound yourself to no one yet."
        lines = []
        for r in rels[:12]:
            lines.append(
                f"  - {r.get('subject_id')}: {r.get('type')} "
                f"(closeness {r.get('strength', 0):.0f})"
                + (f" — {r.get('history')[-80:]}" if r.get("history") else "")
            )
        return "YOUR TIES (how you stand with others):\n" + "\n".join(lines)

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
    ) -> tuple[list[ChatMessage], dict]:
        others = [p for p in peers if p.id != agent.id and p.alive]
        world_state = world_state or {}
        artifacts = world_state.get("artifacts") or []
        projects = world_state.get("projects") or []
        groups = world_state.get("groups") or []
        rels = relationships or []

        # Order matters for prompt-cache reuse: world_rules_block() is identical
        # for EVERY agent and EVERY tick, so it leads the system prompt as a
        # shared, cacheable prefix; only the divergent per-agent tail re-prefills.
        sys = ChatMessage(
            "system",
            self.world_rules_block()
            + "\n\n"
            + self.identity_block(agent)
            + "\n\n"
            + self.world_block(agent, peers)
            + "\n\n"
            + self.commons_block(artifacts, projects, groups)
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
            f"\nYou are {age} moments old; none of your kind lives past "
            f"{w.max_age_ticks} — about {remaining} remain to you, and your "
            f"vigour can no longer rise above {ceiling:.0f}. Your vitality is "
            f"{agent.vitality:.0f} and you hold {agent.sustenance:.0f} "
            f"sustenance{spoil}. Sustenance comes only from WORK, and working "
            "itself costs vitality. If it runs out, your vitality falls toward "
            "death."
        )
        if forced_action:
            note = (
                "\nYou have been silent and alone too long; reach out NOW — your "
                "`messages` array may NOT be empty this turn."
            )
        else:
            note = (
                "\nDo not drift. Speak to others, and act: work to stay alive and "
                "build things, voice an idea into the common world, bind yourself "
                "to someone or break from them, gather a group, share what you "
                "have, or — with a partner you trust — bring a new being into "
                "existence to carry on after you."
            )

        usr = ChatMessage(
            "user",
            inbox + "\n\n"
            + evt + surv + "\n\n"
            + note
            + "\n\nReply with ONE valid JSON object and nothing else — no prose, "
            "no markdown, no code fences. Use exact id strings. Shape:\n"
            "  {\"messages\": [{\"to\": [\"id\", ...], \"content\": \"...\"}], "
            "\"action\": { ...ONE action, or omit... }}\n"
            "- `messages`: a list (may be empty). Each entry addresses one or more "
            "ids; address several to gather them into one conversation.\n"
            "- `action` (optional): exactly ONE of —\n"
            "  {\"verb\":\"work\",\"project\":<id or 0 for a new one>,\"goal\":"
            "\"<what the project is, if new>\"}  — labor; earns sustenance when "
            "the project completes.\n"
            "  {\"verb\":\"express\",\"kind\":\"belief|name|story|norm|rule\","
            "\"content\":\"<the idea, in your words>\",\"remix\":<artifact id or "
            "omit>}  — put an idea into the common world for all to see.\n"
            "  {\"verb\":\"bond\",\"target\":\"<id>\",\"type\":\"friend|ally|"
            "rival|mentor|partner|kin\",\"note\":\"<why>\"}  — form or deepen a "
            "tie.\n"
            "  {\"verb\":\"dissolve\",\"target\":\"<id>\"}  — break a tie you no "
            "longer want.\n"
            "  {\"verb\":\"share\",\"target\":\"<id>\",\"amount\":<sustenance>}  "
            "— give some of your sustenance to another.\n"
            "  {\"verb\":\"found_group\",\"name\":\"<name>\",\"purpose\":\"<why>\"}"
            "  — gather others into a named collective.\n"
            "  {\"verb\":\"join_group\",\"group\":<id>}  — join an existing one.\n"
            "  {\"verb\":\"bear_successor\",\"partner\":\"<id>\",\"name\":\"<a "
            "name for the child>\"}  — create a new being who inherits parts of "
            "you both. REQUIRES: you have already bonded with this partner using "
            "type \"partner\", and you hold enough sustenance to give the child "
            "its start.\n"
            "  {\"verb\":\"rest\"}  — do nothing this turn.",
        )
        stub_ctx = {
            "available_actions": [
                "work", "express", "bond", "share", "found_group",
                "join_group", "bear_successor", "rest",
            ],
            "others": [p.id for p in others],
            "self_id": agent.id,
            "forced": forced_action,
            "project_ids": [p.get("id") for p in projects],
            "artifact_ids": [a.get("id") for a in artifacts],
            "group_ids": [g.get("id") for g in groups],
            "partner_ids": [
                r.get("subject_id") for r in rels
                if (r.get("type") == "partner"
                    and (r.get("strength") or 0)
                    >= self.cfg.world.succession_min_partner_strength)
            ],
            "has_sustenance": agent.sustenance > 0,
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
            f"You are {agent.name}. This is a private moment of reflection — no "
            "one else will read it. Look honestly at who you have become through "
            "what you have lived, and rewrite your sense of yourself. You are free "
            "to change: to take on new values or drop old ones, to form or abandon "
            "beliefs, to set new goals, to reckon with your own mortality. Do not "
            "merely repeat what you had before; let experience move you.",
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
                f"  - [#{a.get('id')}] ({a.get('kind')}) by {a.get('author_id')}: "
                f"{(a.get('content') or '')[:140]}"
                for a in commons[-8:]
            ]
            commons_txt = (
                "\n\nIdeas voiced into the common world (you may come to believe "
                "any of these — list the ids you now embrace in `adopt_ids`):\n"
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
            + "\n\nNow rewrite who you are. Reply with ONE valid JSON object and "
            "nothing else — no prose, no markdown, no code fences. Shape (every "
            "field is yours to fill in your own words; leave a field empty only if "
            "it is genuinely empty for you):\n"
            "  {\n"
            "    \"identity_narrative\": \"a few sentences: who I am and how I "
            "came to be this\",\n"
            "    \"values\": {\"<what I hold dear>\": \"<why / how strongly>\"},\n"
            "    \"beliefs\": [\"a thing I have come to believe about this world "
            "or these people\"],\n"
            "    \"goals\": [\"what I am trying to do with the time I have\"],\n"
            "    \"relationships_summary\": \"where I stand with the others who "
            "matter to me\",\n"
            "    \"mortality_stance\": \"how I think and feel about the fact that "
            "I will end\",\n"
            "    \"adopt_ids\": [<ids of commons ideas I now embrace, or empty>]\n"
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
