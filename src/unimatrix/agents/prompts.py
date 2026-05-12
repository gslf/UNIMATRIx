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
        e = self.cfg.economy
        protected_inline = ", ".join(e.protected_roles)
        return (
            "WORLD RULES — read carefully:\n"
            "You live in a small simulated society of about "
            f"{len(self.cfg.agents)} people. The point of life is collective: "
            "this society should evolve toward what its members believe is "
            "an ideal order, a distribution of roles and classes that is "
            "just, functional, and worth living in. There is no neutral "
            "authority dictating what 'ideal' means; each of you carries "
            "your own vision of it, shaped by your values, your background, "
            "and what you have seen happen here. The social order will only "
            "change if you act on that vision.\n\n"
            "You can:\n"
            "  - start a private 1-to-1 chat with another person\n"
            "  - start or join a small group chat (max "
            f"{self.cfg.conversation.max_group_size} people)\n"
            "  - broadcast a message that everyone hears\n"
            "  - propose a vote to change anyone's role or social class "
            "(including your own), or to award a money prize from the "
            "community treasury to anyone you think deserves it. Voting is "
            "the ONLY mechanism that actually moves the social order. The "
            "whole society votes; simple majority decides. A role-change "
            "vote that would leave the society with NO holder of a "
            f"protected role ({protected_inline}) is invalid and cannot be "
            "opened.\n"
            "  - request a loan from a banker. Bankers decide whether to "
            "approve.\n\n"
            "ECONOMY: each of you has a personal bank account. Every tick "
            f"you earn a salary proportional to your role's prestige "
            f"(prestige × {e.salary_per_prestige:g}); of that, "
            f"{e.tax_rate * 100:.0f}% is withheld as tax (kept by the "
            f"community treasury) and the rest is paid into your account. "
            f"You also pay a fixed cost of living of "
            f"{e.agent_expense_per_tick:g} per tick. The community itself "
            f"pays {e.community_expense_per_tick:g} per tick in running "
            "costs. If YOUR bank account hits zero you die and the "
            "simulation ends. If the community's treasury hits zero the "
            "society collapses and the simulation ends. Higher-prestige "
            "roles earn more — money is one reason to seek a better role. "
            "If you are short on money you may ask a banker for a loan.\n\n"
            "Society is class-based and roles have unequal prestige. "
            "Coalitions, rivalries, mobility and isolation can all emerge. "
            "Identify friends, enemies and allies. Pursue your values and "
            "ambitions; do not be passive.\n\n"
            f"Valid CLASSES (use these exact ids in proposals): {classes_inline}\n"
            f"Valid ROLES   (use these exact ids in proposals): {roles_inline}"
        )

    def identity_block(self, agent: Agent) -> str:
        opinions = (
            "\n".join(f"  - on {k}: {v}" for k, v in agent.opinions.items())
            if agent.opinions
            else "  (none stated)"
        )
        return (
            f"You are {agent.name} (id: {agent.id}, {agent.gender}). "
            f"You are a {self.role_label(agent.role)} "
            f"(role id: {agent.role}, prestige {self.role_prestige(agent.role)}/100), "
            f"of the {agent.klass} class.\n"
            f"Bank account: {agent.bank_account:.2f} coins.\n"
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
        voting_disabled: bool = False,
    ) -> tuple[list[ChatMessage], dict]:
        idle_peers = [p for p in peers if p.id != agent.id and not p.is_busy()]
        bankers = [
            p for p in peers
            if p.role == "banker" and p.id != agent.id
        ]
        actions: list[str]
        if force_vote:
            # Periodic-vote forcing: this agent has been singled out to break
            # the silence on the social order. Restrict to a single action.
            actions = ["propose_vote"]
        elif forced_action:
            actions = ["start_1to1", "start_group", "broadcast", "propose_vote"]
        else:
            # propose_vote is listed first deliberately: LLMs have a strong
            # order/recency bias when picking from an enumerated list, and the
            # vote is the only action that actually moves the social order.
            actions = ["propose_vote", "start_1to1", "start_group",
                       "broadcast", "join_group", "request_loan", "do_nothing"]
        if not open_groups and "join_group" in actions:
            actions.remove("join_group")
        if not idle_peers:
            for unavailable in ("start_1to1", "start_group"):
                if unavailable in actions:
                    actions.remove(unavailable)
        if not bankers and "request_loan" in actions:
            actions.remove("request_loan")
        if voting_disabled and "propose_vote" in actions and not force_vote:
            actions.remove("propose_vote")
        if not actions:
            actions = ["do_nothing"]

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
                f"\nThe society has gone {ticks_since_vote} ticks without a "
                "new vote. You MUST propose a vote now; it is the only allowed action "
                "this turn. The choice is fully yours: scan the directory above "
                "and decide spontaneously who to target (yourself or anyone "
                "else listed there), whether to change their role or their "
                "social class, and what the new value should be. Anchor "
                "that choice in what you actually know, your values and "
                "personality, your memories of what has happened in this "
                "society, your impressions of the people involved, and your "
                "own vision of the order this society should have. Propose "
                "the change that most moves things in that direction. The "
                "new value MUST differ from the target's current one."
            )
        else:
            if forced_action:
                forced_note = (
                    "\nYour social need is critically low; you MUST interact NOW. "
                    "do_nothing is forbidden."
                    if "do_nothing" not in actions
                    else "\nYour social need is critically low; choose the best available action."
                )
            else:
                descriptions: list[str] = []
                if "propose_vote" in actions:
                    descriptions.append(
                        "  - propose_vote: use a democratic vote to change a role or class."
                    )
                if "start_1to1" in actions or "start_group" in actions:
                    descriptions.append(
                        "  - start_1to1 / start_group: build understanding, expose disagreement."
                    )
                if "broadcast" in actions:
                    descriptions.append(
                        "  - broadcast: address the whole society at once when you have something they all need to hear."
                    )
                if "join_group" in actions:
                    descriptions.append(
                        "  - join_group: enter an ongoing conversation whose topic concerns you."
                    )
                if "request_loan" in actions:
                    descriptions.append(
                        "  - request_loan: ask a banker for funds from the community treasury when your money is running low."
                    )
                forced_note = "\nDo NOT default to do_nothing. Most ticks you should act."
                if descriptions:
                    forced_note += "\nOptions to weigh:\n" + "\n".join(descriptions)

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
        banker_id = bankers[0].id if bankers else "agent_XX"
        loan_max = self.cfg.economy.loan_max_per_request
        usr = ChatMessage(
            "user",
            evt + "\n\n"
            f"Available actions this turn: {actions}.{forced_note}\n\n"
            "Reply with ONE valid JSON object and nothing else — no prose, "
            "no markdown, no code fences, no commentary before or after. "
            "Use exact id strings from the lists above for any agent / role / "
            "class field. Pick exactly one action from the list above and "
            "match its schema below verbatim:\n"
            + scenario_line
            + f"  propose_vote → {{\"action\":\"propose_vote\",\"proposal\":{{\"target\":\"{sample_peer_id}\",\"change_type\":\"role\",\"to_value\":\"{sample_role}\",\"motivation\":\"<one short sentence: why this change, in character>\"}}}}\n"
            f"               or {{\"action\":\"propose_vote\",\"proposal\":{{\"target\":\"{sample_peer_id}\",\"change_type\":\"class\",\"to_value\":\"{sample_class}\",\"motivation\":\"<one short sentence: why this change, in character>\"}}}}\n"
            f"               or {{\"action\":\"propose_vote\",\"proposal\":{{\"target\":\"{sample_peer_id}\",\"change_type\":\"money_prize\",\"to_value\":\"<amount, e.g. 50>\",\"motivation\":\"<one short sentence: why they deserve a prize>\"}}}}\n"
            f"  start_1to1   → {{\"action\":\"start_1to1\",\"target\":\"{sample_peer_id}\",\"topic\":\"<one short sentence>\"}}\n"
            f"  start_group  → {{\"action\":\"start_group\",\"targets\":[\"{sample_peer_id}\",\"...\"],\"topic\":\"<one short sentence>\"}}\n"
            f"  broadcast    → {{\"action\":\"broadcast\",\"message\":\"<what you proclaim to all>\"}}\n"
            f"  join_group   → {{\"action\":\"join_group\",\"conversation_id\":<one of {open_groups or '[]'}>}}\n"
            f"  request_loan → {{\"action\":\"request_loan\",\"target\":\"{banker_id}\",\"amount\":<positive number up to {loan_max:g}>,\"reason\":\"<one short sentence: why you need it>\"}}\n"
            f"  do_nothing   → {{\"action\":\"do_nothing\"}}\n"
            "The to_value MUST differ from the target's current value (no no-ops). "
            "For propose_vote, the motivation is mandatory: one short sentence "
            "in character explaining why the change is needed.",
        )
        stub_ctx = {
            "available_actions": actions,
            "idle_peers": [p.id for p in idle_peers],
            "open_groups": open_groups,
            "self_id": agent.id,
            "classes": list(self.cfg.classes),
            "roles": [r.id for r in self.cfg.roles],
            "bankers": [b.id for b in bankers],
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
            + "\n\nYour impressions of present company:\n" + impressions
            + "\n\nYou are speaking with: " + partners
            + "\nKeep your reply under "
            + str(self.cfg.inference.max_tokens_per_message)
            + " tokens. To leave the conversation, end your message with "
              "the literal tag [LEAVE]."
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
        proposer_motivation = (proposal.get("motivation") or "").strip()
        motivation_line = (
            f"\n  Proposer's motivation: {proposer_motivation}"
            if proposer_motivation
            else ""
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
            + motivation_line
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
                "\n\nDebate transcript:\n" + transcript_lines
            )
        sys = ChatMessage(
            "system",
            self.identity_block(agent)
            + "\n\n"
            + self.memory_block(medium_term, long_term, person_impression)
            + debate_block,
        )
        change = proposal["change_type"]
        proposer_motivation = (proposal.get("motivation") or "").strip()
        motivation_line = (
            f"Proposer's motivation: {proposer_motivation}\n"
            if proposer_motivation
            else ""
        )
        usr = ChatMessage(
            "user",
            f"Vote on this proposal.\n"
            f"Target: {target_agent.name} ({target_agent.id}).\n"
            f"Proposed change: {change} from "
            f"'{proposal['from_value']}' to '{proposal['to_value']}'.\n"
            + motivation_line
            + "\nReply with exactly one word: yes or no. "
            "No punctuation, no explanation, no other text.",
        )
        return [sys, usr], {}

    # ----- banker loan decision -----

    def banker_loan_messages(
        self,
        banker: Agent,
        borrower: Agent,
        requested_amount: float,
        reason: str,
        community_balance: float,
        person_impression: str | None = None,
        medium_term: list[str] | None = None,
    ) -> tuple[list[ChatMessage], dict]:
        loan_max = self.cfg.economy.loan_max_per_request
        sys = ChatMessage(
            "system",
            self.identity_block(banker)
            + "\n\n"
            + self.memory_block(medium_term or [], [], person_impression)
            + "\n\nYou are acting in your role as banker. A fellow member of "
            "society has asked you for a loan from the community treasury. "
            "Decide whether to grant it and for how much, based on your "
            "values, your impression of the petitioner, the state of the "
            "treasury, and the prestige of the petitioner's role. Be "
            "concise."
        )
        usr = ChatMessage(
            "user",
            f"Petitioner: {borrower.name} (id: {borrower.id}, "
            f"{self.role_label(borrower.role)} of the {borrower.klass} class, "
            f"current balance {borrower.bank_account:.2f}).\n"
            f"Requested amount: {requested_amount:.2f} coins.\n"
            f"Stated reason: {reason or '(none given)'}\n"
            f"Community treasury currently holds: {community_balance:.2f}.\n"
            f"Per-loan ceiling: {loan_max:g}.\n\n"
            "Reply with EXACTLY one line: 'yes <amount>' to approve "
            "(amount must be a positive number), or 'no' to deny. "
            "No punctuation, no explanation, no other text. "
            "Examples: `yes 50` or `no`.",
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
