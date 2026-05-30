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
        ordinary = self.cfg.ordinary_role_ids()
        roles_by_prestige = sorted(
            (r for r in self.cfg.roles if r.id in set(ordinary)),
            key=lambda r: -r.prestige,
        )
        ordinary_inline = ", ".join(
            f"{r.id} (prestige {r.prestige})" for r in roles_by_prestige
        )
        classes_inline = ", ".join(self.cfg.classes)
        e = self.cfg.economy
        office_ids = self.cfg.office_ids()
        office_label = {
            "legislative": office_ids[0] if len(office_ids) > 0 else "?",
            "judicial": office_ids[1] if len(office_ids) > 1 else "?",
            "financial": office_ids[2] if len(office_ids) > 2 else "?",
        }
        interval = self.cfg.voting.election_interval_ticks
        op = self.cfg.office_powers
        return (
            "WORLD RULES — read carefully:\n"
            "You live in a small simulated society of about "
            f"{len(self.cfg.agents)} people. The point of life is collective: "
            "this society should evolve toward what its members believe is "
            "an ideal order. There is no neutral authority dictating what "
            "'ideal' means; each of you carries your own vision of it, shaped "
            "by your values, your background, and what you have seen happen "
            "here. You shape the order through your daily actions.\n\n"
            "TWO PERSONAL STANDINGS drive your place in society:\n"
            "  - PRESTIGE (0–100) determines your ROLE. Your role is set "
            "automatically to the highest ordinary role your prestige earns "
            "you — gain prestige and you rise through the roles; lose it and "
            "you fall.\n"
            "  - POPULARITY (0–100) together with your BANK BALANCE determines "
            "your CLASS. To hold a class you must keep BOTH your popularity AND "
            "your balance at or above that class's thresholds; if EITHER drops "
            "below, you are demoted to a lower class.\n\n"
            "THREE ELECTED OFFICES wield real power and sit in the top class "
            "while in office:\n"
            f"  - {office_label['legislative']} (head of state): each tick may "
            f"raise or lower the PRESTIGE of several people by up to "
            f"{op.senator_prestige_power:g} — the kingmaker of the role order.\n"
            f"  - {office_label['judicial']} (judge): each tick may raise or "
            f"lower the POPULARITY of several people by up to "
            f"{op.judge_popularity_power:g}, and may fine the convicted.\n"
            f"  - {office_label['financial']} (banker): each tick may move "
            "treasury money to or from several people, and approves loans.\n"
            f"A society-wide ELECTION is held automatically every {interval} "
            "ticks: everyone votes for who should hold each office. When a new "
            "officeholder is elected, the voters also decide what ordinary role "
            "the outgoing officeholder takes next. Elections are the only vote; "
            "you cannot call one.\n\n"
            "WHAT YOU CAN DO each tick:\n"
            "  - message: send messages to one or more people (up to "
            f"{self.cfg.messaging.max_recipients_per_message} recipients each); "
            "they read them next turn and may reply. This is your main way to "
            "build alliances and scheme.\n"
            "  - AND optionally ONE of:\n"
            "  - praise / denounce: nudge one or more people's popularity "
            "and/or prestige up or down (a small amount — far less than an "
            "office can)\n"
            "  - steal: try to take coins from someone (RISKY — you may be "
            "caught and lose popularity)\n"
            "  - gift: give coins to someone (forge alliances, reward, bribe)\n"
            "  - request_loan: ask the banker for funds\n"
            "  - sabotage: try to block an officeholder's next use of power "
            "(RISKY — you may be caught and lose popularity)\n"
            "  - do_nothing\n\n"
            "ECONOMY: each of you has a personal bank account. Every tick "
            f"you earn a salary proportional to your role's prestige "
            f"(prestige × {e.salary_per_prestige:g}); of that, "
            f"{e.tax_rate * 100:.0f}% is withheld as tax and the rest is paid "
            f"to you. You also pay a fixed cost of living of "
            f"{e.agent_expense_per_tick:g} per tick. If the community's "
            "treasury collapses the simulation ends. Higher-prestige roles "
            "earn more.\n\n"
            "Coalitions, rivalries, mobility and isolation can all emerge. "
            "Identify friends, enemies and allies. Pursue your values and "
            "ambitions; do not be passive.\n\n"
            f"CLASSES (highest → lowest): {classes_inline}\n"
            f"ORDINARY ROLES (by prestige): {ordinary_inline}\n"
            f"ELECTED OFFICES: {', '.join(office_ids)}"
        )

    def identity_block(self, agent: Agent) -> str:
        opinions = (
            "\n".join(f"  - on {k}: {v}" for k, v in agent.opinions.items())
            if agent.opinions
            else "  (none stated)"
        )
        office_line = ""
        if agent.office:
            office_line = (
                f"You currently hold the ELECTED OFFICE of "
                f"{self.role_label(agent.office)} (office id: {agent.office}) — "
                "you wield its special power each tick and sit in the top class "
                "while in office.\n"
            )
        return (
            f"You are {agent.name} (id: {agent.id}, {agent.gender}). "
            f"You are a {self.role_label(agent.role)} "
            f"(role id: {agent.role}, prestige {self.role_prestige(agent.role)}/100), "
            f"of the {agent.klass} class.\n"
            + office_line
            + f"Your prestige: {agent.prestige:.0f}/100 (drives your role). "
            f"Your popularity: {agent.popularity:.0f}/100 (with your balance, "
            "drives your class).\n"
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

        Everyone is reachable by message at any time (no one is ever locked in
        a dialogue), so this is a single flat directory. `max_listed`, when set,
        truncates the list.
        """
        peers_list = [p for p in peers if p.id != agent.id]
        if not peers_list:
            return "Society directory: (you are alone)"

        def _line(p: Agent) -> str:
            office_tag = f", OFFICE: {p.office}" if p.office else ""
            return (
                f"- {p.name} [id: {p.id}] — {self.role_label(p.role)} "
                f"(role id: {p.role}, class: {p.klass}{office_tag}) — "
                f"prestige {p.prestige:.0f}, popularity {p.popularity:.0f}, "
                f"balance {p.bank_account:.0f}"
            )

        visible = peers_list if max_listed is None else peers_list[:max_listed]
        lines = [_line(p) for p in visible]
        if max_listed is not None and len(peers_list) > max_listed:
            lines.append(f"  ...and {len(peers_list) - max_listed} others")
        return (
            "Society directory (everyone you can message or target by id):\n"
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
    ) -> tuple[list[ChatMessage], dict]:
        others = [p for p in peers if p.id != agent.id]
        idle_peers = others  # everyone is reachable by message
        financial_office = self.cfg.office_for_power("financial")
        bankers = [p for p in others if p.office == financial_office]
        officeholders = [p for p in others if p.office is not None]
        my_power = self.cfg.power_of_office(agent.office) if agent.office else None
        power_action = {
            "legislative": "decree",
            "judicial": "ruling",
            "financial": "policy",
        }.get(my_power) if my_power else None

        # The single optional power/economy action (messaging is separate).
        actions = ["praise", "denounce", "request_loan", "steal", "gift",
                   "sabotage", "do_nothing"]
        # Offer the office power first (LLM primacy/recency bias → act).
        if power_action:
            actions.insert(0, power_action)
        if not bankers and "request_loan" in actions:
            actions.remove("request_loan")
        if not officeholders and "sabotage" in actions:
            actions.remove("sabotage")
        if not others:
            for unavailable in ("praise", "denounce", "steal", "gift"):
                if unavailable in actions:
                    actions.remove(unavailable)
        if not actions:
            actions = ["do_nothing"]

        # Order matters for llama.cpp / LM Studio prompt-cache reuse. world_rules_block()
        # is identical for EVERY agent and EVERY tick (it takes no agent/peer args),
        # so it leads the system prompt as a shared, cacheable prefix: after the first
        # agent fills a slot's KV cache with it, the rest of the batch (and later ticks)
        # reuse that prefix and only prefill the divergent per-agent tail. Putting the
        # per-agent identity first — as before — broke the prefix on token 1 and forced
        # a full re-prefill of all ~2900 tokens for each of the N agents.
        sys = ChatMessage(
            "system",
            self.world_rules_block()
            + "\n\n"
            + self.identity_block(agent)
            + "\n\n"
            + self.world_block(agent, peers)
            + "\n\n"
            + self.memory_block(medium_term, long_term, None),
        )
        inbox = (
            "MESSAGES YOU RECEIVED (read them and reply by addressing the "
            "senders in your `messages`):\n"
            + "\n".join(f"  - {line}" for line in inbox_lines)
            if inbox_lines
            else "Your inbox is empty this turn."
        )
        evt = (
            "Recent public events:\n" + "\n".join(f"  - {e}" for e in recent_events[-8:])
            if recent_events
            else "No recent public events."
        )
        if forced_action:
            note = (
                "\nYour social need is critically low; you MUST reach out NOW — "
                "your `messages` array may NOT be empty this turn."
            )
        else:
            note = (
                "\nDo NOT be passive. Most turns you should send at least one "
                "message to build coalitions, answer those who wrote you, raise "
                "allies or undermine rivals — and optionally take one action."
            )

        sample = others[0].id if others else "agent_XX"
        banker_id = bankers[0].id if bankers else "agent_XX"
        office_target = officeholders[0].id if officeholders else "agent_XX"
        loan_max = self.cfg.economy.loan_max_per_request
        op = self.cfg.office_powers
        ap = self.cfg.agent_powers

        # Only show the schema for actions actually available this turn.
        lines: list[str] = []
        if power_action == "decree":
            lines.append(
                f"  decree → {{\"action\":\"decree\",\"targets\":[\"{sample}\"],"
                f"\"direction\":\"raise\"|\"lower\"}}  "
                f"(±{op.senator_prestige_power:g} prestige to each target)"
            )
        if power_action == "ruling":
            lines.append(
                f"  ruling → {{\"action\":\"ruling\",\"targets\":[\"{sample}\"],"
                f"\"verdict\":\"sanction\"|\"vindicate\"}}  "
                f"(sanction: −{op.judge_popularity_power:g} popularity + fine; "
                f"vindicate: +{op.judge_popularity_power:g} popularity)"
            )
        if power_action == "policy":
            lines.append(
                f"  policy → {{\"action\":\"policy\",\"targets\":[\"{sample}\"],"
                f"\"direction\":\"subsidy\"|\"levy\",\"amount\":"
                f"<up to {op.banker_transfer_max:g}>}}  "
                "(subsidy: treasury→agent; levy: agent→treasury)"
            )
        if "praise" in actions:
            lines.append(
                f"  praise → {{\"action\":\"praise\",\"targets\":[\"{sample}\"],"
                "\"aspect\":\"popularity\"|\"prestige\"|\"both\"}  "
                f"(+{self.cfg.mobility.influence_step:g} to each)"
            )
        if "denounce" in actions:
            lines.append(
                f"  denounce → {{\"action\":\"denounce\",\"targets\":[\"{sample}\"],"
                "\"aspect\":\"popularity\"|\"prestige\"|\"both\"}  "
                f"(−{self.cfg.mobility.influence_step:g} to each)"
            )
        if "request_loan" in actions:
            lines.append(
                f"  request_loan → {{\"action\":\"request_loan\",\"target\":"
                f"\"{banker_id}\",\"amount\":<up to {loan_max:g}>,\"reason\":"
                "\"<one short sentence>\"}"
            )
        if "steal" in actions:
            lines.append(
                f"  steal → {{\"action\":\"steal\",\"target\":\"{sample}\","
                f"\"amount\":<up to {ap.steal_max:g}>}}  (RISKY: may be caught "
                "and lose popularity)"
            )
        if "gift" in actions:
            lines.append(
                f"  gift → {{\"action\":\"gift\",\"target\":\"{sample}\","
                f"\"amount\":<up to {ap.gift_max:g}>}}"
            )
        if "sabotage" in actions:
            lines.append(
                f"  sabotage → {{\"action\":\"sabotage\",\"target\":"
                f"\"{office_target}\"}}  (RISKY: may be caught and lose popularity)"
            )
        if "do_nothing" in actions:
            lines.append("  do_nothing → {\"action\":\"do_nothing\"}")

        action_menu = (
            "\n".join(lines) if lines
            else "  do_nothing → no action this turn"
        )
        usr = ChatMessage(
            "user",
            inbox + "\n\n"
            + evt + "\n\n"
            + "Each turn you may do BOTH: (1) send MESSAGES to one or more "
            "people (they read them next turn and may reply), and (2) optionally "
            "take ONE power/economy ACTION."
            + note
            + "\n\nReply with ONE valid JSON object and nothing else — no prose, "
            "no markdown, no code fences. Use exact id strings from the directory "
            "above. Shape:\n"
            "  {\"messages\": [{\"to\": [\"agent_id\", ...], \"content\": "
            "\"...\"}, ...], \"action\": \"<one of below or do_nothing>\", "
            "...action fields...}\n"
            "- `messages` is a list (may be empty); each entry addresses one or "
            "more agent ids. Send to several people to start a group thread.\n"
            "- `action` is exactly ONE of the following; put ITS fields at the "
            "top level of the object alongside `messages`:\n"
            + action_menu,
        )
        stub_ctx = {
            "available_actions": actions,
            "idle_peers": [p.id for p in idle_peers],
            "others": [p.id for p in others],
            "officeholders": [p.id for p in officeholders],
            "self_id": agent.id,
            "self_office": agent.office,
            "power_action": power_action,
            "forced": forced_action,
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

    # ----- election debate (pre-ballot speech) -----

    def _debate_block(self, transcript: list[dict]) -> str:
        if not transcript:
            return "\n\nNobody has spoken yet."
        lines = "\n".join(
            f"- {e.get('speaker_name', e.get('speaker_id', '?'))}: "
            f"{e.get('text', '')}"
            for e in transcript
        )
        return "\n\nDebate so far:\n" + lines

    def election_debate_messages(
        self,
        agent: Agent,
        office_ids: list[str],
        prior_transcript: list[dict],
        medium_term: list[str],
        round_index: int = 0,
        rounds_total: int = 1,
        max_tokens: int = 120,
    ) -> tuple[list[ChatMessage], dict]:
        offices_inline = ", ".join(self.role_label(o) for o in office_ids)
        sys = ChatMessage(
            "system",
            self.identity_block(agent)
            + "\n\n"
            + self.memory_block(medium_term, [], None)
            + "\n\nA society-wide ELECTION is about to be held for these "
            f"offices: {offices_inline}. Before the ballots, the society holds "
            "a brief debate: each member speaks once per round. Speak in "
            "character — argue who should hold which office and why, given your "
            "values, ambitions and your standing relationships. Do NOT cast a "
            "ballot yet. "
            f"Keep it under {max_tokens} tokens, one or two sentences. Output a "
            "single line of plain text — no JSON, no labels, no quotes."
        )
        usr = ChatMessage(
            "user",
            f"Election debate (round {round_index + 1} of {rounds_total})."
            + self._debate_block(prior_transcript)
            + "\n\nYour speech:",
        )
        return [sys, usr], {}

    # ----- election: office ballot -----

    def _candidate_menu(self, candidates: list[Agent]) -> str:
        return "\n".join(
            f"  - {c.id}  ({c.name}, currently {self.role_label(c.role)}, "
            f"prestige {c.prestige:.0f}, popularity {c.popularity:.0f})"
            for c in candidates
        )

    def election_ballot_messages(
        self,
        agent: Agent,
        office: str,
        candidates: list[Agent],
        holder: Agent | None,
        medium_term: list[str],
        debate_transcript: list[dict] | None = None,
    ) -> tuple[list[ChatMessage], dict]:
        sys = ChatMessage(
            "system",
            self.identity_block(agent)
            + "\n\n"
            + self.memory_block(medium_term, [], None)
            + self._debate_block(debate_transcript or []),
        )
        holder_line = (
            f"Current {self.role_label(office)}: {holder.name} ({holder.id}).\n"
            if holder is not None
            else f"The office of {self.role_label(office)} is currently vacant.\n"
        )
        usr = ChatMessage(
            "user",
            f"ELECTION — vote for the office of {self.role_label(office)} "
            f"(office id: {office}).\n"
            + holder_line
            + "Candidates (you may re-elect the current holder):\n"
            + self._candidate_menu(candidates)
            + "\n\nReply with EXACTLY one candidate agent id from the list "
            "above (for example: " + candidates[0].id + "). "
            "No JSON, no name, no explanation, no other text — just the id.",
        )
        return [sys, usr], {}

    # ----- election: reassignment ballot -----

    def election_reassign_messages(
        self,
        agent: Agent,
        office: str,
        holder: Agent,
        ordinary_role_ids: list[str],
        medium_term: list[str],
    ) -> tuple[list[ChatMessage], dict]:
        sys = ChatMessage(
            "system",
            self.identity_block(agent)
            + "\n\n"
            + self.memory_block(medium_term, [], None),
        )
        role_menu = "\n".join(
            f"  - {rid}  ({self.role_label(rid)}, prestige "
            f"{self.role_prestige(rid)})"
            for rid in ordinary_role_ids
        )
        usr = ChatMessage(
            "user",
            f"{holder.name} ({holder.id}) is leaving the office of "
            f"{self.role_label(office)}. The society decides what ordinary role "
            "they take next. Options:\n"
            + role_menu
            + "\n\nReply with EXACTLY one role id from the list above (for "
            f"example: {ordinary_role_ids[0]}). No JSON, no explanation, no "
            "other text — just the id.",
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
