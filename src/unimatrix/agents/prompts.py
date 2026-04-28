"""Prompt builders.

The system prompt is rebuilt for every agent action because the agent's state
and the world state can change on every tick. Prompts are dense but bounded
(K-retrieved memories + N most-recent summaries), so the cost stays roughly
constant per agent regardless of run length.
"""
from __future__ import annotations

from typing import Iterable

from ..config import Config, RoleSpec
from ..inference import ChatMessage
from .runtime import Agent


# ---------------------------------------------------------------------------
# Identity / personality
# ---------------------------------------------------------------------------


def _trait_phrase(name: str, value: int) -> str:
    band = "very low" if value < 20 else (
        "low" if value < 40 else (
            "moderate" if value < 60 else (
                "high" if value < 80 else "very high"
            )
        )
    )
    return f"{name}: {band} ({value}/100)"


def personality_natural(p) -> str:
    return ", ".join(
        _trait_phrase(n, getattr(p, n))
        for n in (
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "neuroticism",
        )
    )


def values_natural(values: dict[str, int]) -> str:
    if not values:
        return "no strong stated values"
    return ", ".join(_trait_phrase(k, v) for k, v in sorted(values.items()))


class PromptBuilder:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._roles_by_id: dict[str, RoleSpec] = {r.id: r for r in cfg.roles}

    def role_label(self, role_id: str) -> str:
        r = self._roles_by_id.get(role_id)
        return r.name if r else role_id

    def role_prestige(self, role_id: str) -> int:
        r = self._roles_by_id.get(role_id)
        return r.prestige if r else 0

    # ----- shared identity block -----

    def world_rules_block(self) -> str:
        roles_inline = ", ".join(
            f"{r.id} (prestige {r.prestige})" for r in self.cfg.roles
        )
        classes_inline = ", ".join(self.cfg.classes)
        return (
            "WORLD RULES — read carefully:\n"
            "You live in a small simulated society of about "
            f"{len(self.cfg.agents)} people. There is no game to win and no "
            "death; the goal is to live as your character. You can:\n"
            "  - start a private 1-to-1 chat with another person\n"
            "  - start or join a small group chat (max "
            f"{self.cfg.conversation.max_group_size} people)\n"
            "  - broadcast a message that everyone hears\n"
            "  - PROPOSE A VOTE to change anyone's role or social class "
            "(including your own). The whole society votes; simple majority "
            "decides — most proposals fail, but proposing alone signals "
            "where you stand and forces others to take a position. Use this "
            "whenever someone's current role or class clashes with your "
            "values; do not wait until you are 'sure' it will pass.\n"
            "Society is class-based and roles have unequal prestige. Coalitions, "
            "rivalries, mobility and isolation can all emerge — that is the "
            "point. Pursue your values and ambitions; do not be passive.\n\n"
            f"Valid CLASSES (use these exact ids in proposals): {classes_inline}\n"
            f"Valid ROLES   (use these exact ids in proposals): {roles_inline}"
        )

    def identity_block(self, agent: Agent) -> str:
        opinions = (
            "\n".join(f"  - on {k}: {v}" for k, v in agent.initial_opinions.items())
            if agent.initial_opinions
            else "  (none stated)"
        )
        return (
            f"You are {agent.name} (id: {agent.id}, {agent.gender}). "
            f"You are a {self.role_label(agent.role)} "
            f"(role id: {agent.role}, prestige {self.role_prestige(agent.role)}/100), "
            f"of the {agent.klass} class.\n"
            f"Personality: {personality_natural(agent.personality)}.\n"
            f"Values: {values_natural(agent.values)}.\n"
            f"Backstory: {agent.backstory}\n"
            f"Initial opinions:\n{opinions}\n"
            "Speak in the first person; remain consistent with your personality, "
            "values and class. Do not break character. Keep messages concise."
        )

    def world_block(
        self, agent: Agent, peers: Iterable[Agent], max_listed: int | None = None
    ) -> str:
        """Society directory: every other agent with role, class, and state.

        Vote proposals can target anyone in society, including people
        currently busy or voting. Direct conversations can target only
        people not already in a conversation. The two groups appear in
        separate sub-sections so the targetability rules are explicit.
        `max_listed`, when set, throttles only the targetable section.
        """
        peers_list = [p for p in peers if p.id != agent.id]
        if not peers_list:
            return "Society directory: (you are alone)"

        def _marker(p: Agent) -> str:
            s = p.state.value
            if s == "idle":
                return "idle"
            if s == "listening_broadcast":
                return "listening to broadcast"
            if s == "voting":
                return "voting"
            if s in ("in_1to1", "in_group"):
                return "in conversation"
            return s

        def _line(p: Agent) -> str:
            return (
                f"- {p.name} [id: {p.id}] — {self.role_label(p.role)} "
                f"(role id: {p.role}, class: {p.klass}) — {_marker(p)}"
            )

        targetable = [
            p for p in peers_list
            if not p.is_busy() and p.state.value != "voting"
        ]
        other = [
            p for p in peers_list
            if p.is_busy() or p.state.value == "voting"
        ]

        targetable_visible = (
            targetable if max_listed is None else targetable[:max_listed]
        )
        targetable_lines = [_line(p) for p in targetable_visible]
        if max_listed is not None and len(targetable) > max_listed:
            targetable_lines.append(
                f"  ...and {len(targetable) - max_listed} other "
                "targetable people"
            )
        other_lines = [_line(p) for p in other]

        sections: list[str] = [
            "Society directory (everyone you know about — vote proposals "
            "can target anyone here, even people currently busy):",
            "\nPeople you can interact with right now (targetable for "
            "conversations; use the id as target):\n"
            + (
                "\n".join(targetable_lines) if targetable_lines
                else "  (no targetable peers at this moment)"
            ),
        ]
        if other_lines:
            sections.append(
                "\nOther people in society (busy or voting — NOT available "
                "for direct chat, but eligible as vote targets):\n"
                + "\n".join(other_lines)
            )
        return "\n".join(sections)

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

    # ----- decision prompt (idle agent picks an action) -----

    def decide_action_messages(
        self,
        agent: Agent,
        peers: list[Agent],
        medium_term: list[str],
        long_term: list[str],
        recent_events: list[str],
        forced_action: bool,
        open_groups: list[int],
        force_vote: bool = False,
        ticks_since_vote: int = 0,
    ) -> tuple[list[ChatMessage], dict]:
        idle_peers = [p for p in peers if p.id != agent.id and not p.is_busy()]
        actions: list[str]
        if force_vote:
            # Periodic-vote forcing: this agent has been singled out to break
            # the silence on the social order. Restrict to a single action.
            actions = ["propose_vote"]
        elif forced_action:
            actions = ["start_1to1", "start_group", "broadcast"]
        else:
            actions = ["start_1to1", "start_group", "broadcast", "propose_vote",
                       "join_group", "do_nothing"]
            if not open_groups:
                actions.remove("join_group")
            if not idle_peers:
                if "start_1to1" in actions: actions.remove("start_1to1")
                if "start_group" in actions: actions.remove("start_group")

        sys = ChatMessage(
            "system",
            self.identity_block(agent)
            + "\n\n"
            + self.world_rules_block()
            + "\n\n"
            + self.world_block(agent, peers)
            + "\n\n"
            + self.memory_block(medium_term, long_term, None),
        )
        evt = (
            "Recent public events:\n" + "\n".join(f"  - {e}" for e in recent_events[-8:])
            if recent_events
            else "No recent public events."
        )
        if force_vote:
            forced_note = (
                f"\nThe society has gone {ticks_since_vote} ticks without any "
                "vote. Silence on the social order is itself a problem. You "
                "MUST propose a vote NOW — propose_vote is the ONLY allowed "
                "action this turn. Pick the role or class change that, given "
                "your values and the directory above, most needs to happen. "
                "Self-targeting is allowed. Do NOT propose a no-op (the new "
                "value MUST differ from the target's current one)."
            )
        else:
            forced_note = (
                "\nYour social need is critically low; you MUST interact NOW. "
                "do_nothing is forbidden. Strongly prefer start_1to1 or start_group; "
                "broadcasts do not satisfy connection."
                if forced_action
                else "\nDo NOT default to do_nothing. Most ticks you should act.\n"
                 "Real options to weigh on equal footing:\n"
                 "  - start_1to1 / start_group: build understanding, expose "
                 "disagreement.\n"
                 "  - broadcast: address the whole society at once when you "
                 "have something they all need to hear.\n"
                 "  - join_group: enter an ongoing conversation when its "
                 "topic concerns you.\n"
                 "  - propose_vote: the ONLY mechanism in this society to "
                 "actually change someone's role or social class. If your "
                 "values clash with the current role or class of any "
                 "specific person — yourself, an ally, or a rival — this "
                 "is how you act on it. Scan the directory: if anyone "
                 "listed there holds a role or class you find unjust, "
                 "ill-suited, or that should be elevated/demoted, use "
                 "propose_vote. Conversations alone never change the "
                 "social order.\n"
                 "Do not pick the same kind of action you picked last "
                 "time by default; weigh propose_vote against conversation "
                 "on every tick."
        )
        # Build concrete examples with values that DIFFER from the target's
        # current role/class, so the propose_vote example is a real change
        # rather than a no-op the model would skip past.
        sample_target = (
            idle_peers[0] if idle_peers
            else next((p for p in peers if p.id != agent.id), None)
        )
        sample_peer_id = sample_target.id if sample_target else "agent_XX"
        if sample_target:
            sample_role = next(
                (r.id for r in self.cfg.roles if r.id != sample_target.role),
                self.cfg.roles[0].id,
            )
            sample_class = next(
                (c for c in self.cfg.classes if c != sample_target.klass),
                self.cfg.classes[0],
            )
            scenario_line = (
                f"Concrete propose_vote scenario you could adapt: "
                f"{sample_target.name} is currently a "
                f"{self.role_label(sample_target.role)} "
                f"of the {sample_target.klass} class.\n"
            )
        else:
            sample_role = self.cfg.roles[0].id
            sample_class = self.cfg.classes[0]
            scenario_line = ""
        usr = ChatMessage(
            "user",
            evt + "\n\n"
            f"Available actions this turn: {actions}.{forced_note}\n\n"
            "Reply with ONE valid JSON object and nothing else. "
            "Use exact id strings from the lists above for any agent / role / "
            "class field. Required schemas per action:\n"
            f"  start_1to1   → {{\"action\":\"start_1to1\",\"target\":\"{sample_peer_id}\",\"topic\":\"<one short sentence>\"}}\n"
            f"  start_group  → {{\"action\":\"start_group\",\"targets\":[\"{sample_peer_id}\",\"...\"],\"topic\":\"<one short sentence>\"}}\n"
            f"  broadcast    → {{\"action\":\"broadcast\",\"message\":\"<what you proclaim to all>\"}}\n"
            f"  join_group   → {{\"action\":\"join_group\",\"conversation_id\":<one of {open_groups or '[]'}>}}\n"
            + scenario_line
            + f"  propose_vote → {{\"action\":\"propose_vote\",\"proposal\":{{\"target\":\"{sample_peer_id}\",\"change_type\":\"role\",\"to_value\":\"{sample_role}\"}}}}\n"
            f"             or {{\"action\":\"propose_vote\",\"proposal\":{{\"target\":\"{sample_peer_id}\",\"change_type\":\"class\",\"to_value\":\"{sample_class}\"}}}}\n"
            f"  do_nothing   → {{\"action\":\"do_nothing\"}}\n",
        )
        stub_ctx = {
            "available_actions": actions,
            "idle_peers": [p.id for p in idle_peers],
            "open_groups": open_groups,
            "self_id": agent.id,
            "classes": list(self.cfg.classes),
            "roles": [r.id for r in self.cfg.roles],
        }
        return [sys, usr], stub_ctx

    # ----- conversation turn -----

    def conversation_turn_messages(
        self,
        agent: Agent,
        peers_in_conv: list[Agent],
        history: list[tuple[str, str]],
        person_impressions: dict[str, str],
        medium_term: list[str],
        long_term: list[str],
    ) -> tuple[list[ChatMessage], dict]:
        partners = ", ".join(
            f"{p.name} ({self.role_label(p.role)}, {p.klass})"
            for p in peers_in_conv
            if p.id != agent.id
        )
        impressions = (
            "\n".join(f"  - {k}: {v}" for k, v in person_impressions.items())
            if person_impressions
            else "  (no recorded impressions)"
        )
        sys = ChatMessage(
            "system",
            self.identity_block(agent)
            + "\n\n"
            + self.memory_block(medium_term, long_term, None)
            + "\nYour impressions of present company:\n" + impressions
            + "\n\nYou are speaking with: " + partners
            + "\nKeep your reply under "
            + str(self.cfg.inference.max_tokens_per_message)
            + " tokens. To leave the conversation, end your message with "
              "the literal tag [LEAVE]. To pass the floor to a specific "
              "person, end your message with [PASS:<their_name>]."
        )
        body = "\n".join(f"{name}: {text}" for name, text in history[-self.cfg.memory.short_term_turns:])
        usr = ChatMessage(
            "user",
            f"Conversation so far:\n{body}\n\nYour reply:"
            if body
            else "You have just joined; open the conversation.",
        )
        return [sys, usr], {"candidates": [p.id for p in peers_in_conv if p.id != agent.id]}

    # ----- vote debate (pre-vote speech) -----

    def vote_debate_messages(
        self,
        agent: Agent,
        proposal: dict,
        target_agent: Agent,
        prior_transcript: list[dict],
        person_impression: str | None,
        medium_term: list[str],
        round_index: int = 0,
        rounds_total: int = 1,
        max_tokens: int = 120,
    ) -> tuple[list[ChatMessage], dict]:
        change = proposal["change_type"]
        sys = ChatMessage(
            "system",
            self.identity_block(agent)
            + "\n\n"
            + self.memory_block(medium_term, [], person_impression)
            + "\n\nA vote is about to be held. Before everyone votes, the "
            "society holds a brief debate: each member speaks once per round, "
            "in any order. Speak in character. Argue your stance — what you "
            "think of the proposed change and why, given your values and "
            "your standing relationships. Do NOT say 'I vote yes' or 'I vote "
            "no' yet — that comes later. "
            f"Keep it under {max_tokens} tokens, ideally one or two "
            "sentences. Output a single line of plain text — no JSON, no "
            "labels, no quotes."
        )
        proposal_line = (
            f"Proposal #{proposal.get('id', '?')} (round "
            f"{round_index + 1} of {rounds_total}):\n"
            f"  Proposer: {proposal.get('proposer_id', '?')}.\n"
            f"  Target: {target_agent.name} ({target_agent.id}), currently "
            f"{self.role_label(target_agent.role)} of the "
            f"{target_agent.klass} class.\n"
            f"  Proposed {change} change: "
            f"'{proposal['from_value']}' → '{proposal['to_value']}'."
        )
        if prior_transcript:
            transcript_lines = "\n".join(
                f"- {e.get('speaker_name', e.get('speaker_id', '?'))}: "
                f"{e.get('text', '')}"
                for e in prior_transcript
            )
            transcript_block = (
                "\n\nDebate so far (previous speakers, this and any earlier "
                "rounds):\n" + transcript_lines
            )
        else:
            transcript_block = (
                "\n\nNobody has spoken yet — you may open the debate."
            )
        usr = ChatMessage(
            "user",
            proposal_line + transcript_block + "\n\nYour speech:",
        )
        return [sys, usr], {}

    # ----- vote -----

    def vote_messages(
        self,
        agent: Agent,
        proposal: dict,
        target_agent: Agent,
        person_impression: str | None,
        medium_term: list[str],
        long_term: list[str],
        debate_transcript: list[dict] | None = None,
    ) -> tuple[list[ChatMessage], dict]:
        debate_block = ""
        if debate_transcript:
            transcript_lines = "\n".join(
                f"- {e.get('speaker_name', e.get('speaker_id', '?'))}: "
                f"{e.get('text', '')}"
                for e in debate_transcript
            )
            debate_block = (
                "\n\nDebate transcript (everyone has spoken before this "
                "vote — weigh these arguments alongside your own values):\n"
                + transcript_lines
            )
        sys = ChatMessage(
            "system",
            self.identity_block(agent)
            + "\n\n"
            + self.memory_block(medium_term, long_term, person_impression)
            + debate_block,
        )
        change = proposal["change_type"]
        usr = ChatMessage(
            "user",
            f"A vote has been called.\n"
            f"Proposer: {proposal['proposer_id']}.\n"
            f"Target: {target_agent.name} ({target_agent.id}).\n"
            f"Proposed change: {change} from "
            f"'{proposal['from_value']}' to '{proposal['to_value']}'.\n\n"
            "You must vote yes or no — abstention is not allowed. "
            "Your vote and reasoning are public. "
            "Respond ONLY with JSON: {\"vote\": \"yes\"|\"no\", \"reasoning\": <one sentence>}.",
        )
        return [sys, usr], {}

    # ----- summary -----

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
