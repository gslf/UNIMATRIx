"""Graph renderer — the seven analytics views.

All graphs are rendered on demand from the SQLite store and saved as PNG
under runs/<run>/graphs/. Matplotlib is configured to use the headless 'Agg'
backend so we can render without a display.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  
import networkx as nx 

from ..config import Config
from ..persistence import RunStore


GRAPH_NAMES = (
    "class_distribution",
    "role_distribution",
    "social_network",
    "voting_timeline",
    "mobility_heatmap",
    "isolation",
    "message_volume",
    "balances",
)


def _parse_iso(ts: str) -> datetime:
    # SQLite stores ISO8601; tolerate trailing Z.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


class GraphRenderer:
    def __init__(self, cfg: Config, store: RunStore, out_dir: Path) -> None:
        self.cfg = cfg
        self.store = store
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        # color map per class (consistent across graphs)
        cmap = plt.get_cmap("tab10")
        self.class_color = {
            c: cmap(i % 10) for i, c in enumerate(cfg.classes)
        }

    # ----- dispatch -----

    def render(self, name: str) -> Path:
        method = getattr(self, f"_render_{name}", None)
        if method is None:
            raise KeyError(f"unknown graph {name}")
        return method()

    def render_all(self) -> dict[str, Path]:
        return {n: self.render(n) for n in GRAPH_NAMES}

    # ----- 1. class distribution over time -----

    def _render_class_distribution(self) -> Path:
        changes = self.store.list_status_changes()
        # Build a per-agent timeline of class assignments (initial + class changes).
        agent_class: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        for ch in changes:
            if ch["change_type"] in ("class", "initial"):
                if ch["change_type"] == "initial" and ch["to_value"] not in self.cfg.classes:
                    continue
                agent_class[ch["agent_id"]].append((_parse_iso(ch["ts"]), ch["to_value"]))
        if not any(agent_class.values()):
            return self._empty_plot("class_distribution", "No data")
        all_ts = sorted({t for series in agent_class.values() for t, _ in series})
        # bucketize per minute
        start = all_ts[0]
        end = all_ts[-1]
        buckets = []
        cur = start
        while cur <= end:
            buckets.append(cur)
            # advance ~60 seconds
            cur = cur.fromtimestamp(cur.timestamp() + 60.0, cur.tzinfo)
        if buckets[-1] < end:
            buckets.append(end)
        counts: dict[str, list[int]] = {c: [0] * len(buckets) for c in self.cfg.classes}
        for aid, series in agent_class.items():
            series.sort()
            j = 0
            cur_class = series[0][1]
            for i, b in enumerate(buckets):
                while j + 1 < len(series) and series[j + 1][0] <= b:
                    j += 1
                    cur_class = series[j][1]
                if cur_class in counts:
                    counts[cur_class][i] += 1
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.stackplot(
            buckets,
            *[counts[c] for c in self.cfg.classes],
            labels=self.cfg.classes,
            colors=[self.class_color[c] for c in self.cfg.classes],
        )
        ax.set_title("Class distribution over time")
        ax.set_xlabel("time")
        ax.set_ylabel("agents")
        ax.legend(loc="upper right", fontsize=8)
        fig.autofmt_xdate()
        return self._save(fig, "class_distribution")

    # ----- 2. role distribution over time -----

    def _render_role_distribution(self) -> Path:
        changes = self.store.list_status_changes()
        agent_role: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
        role_ids = [r.id for r in self.cfg.roles]
        for ch in changes:
            if ch["change_type"] in ("role", "initial"):
                if ch["change_type"] == "initial" and ch["to_value"] not in role_ids:
                    continue
                agent_role[ch["agent_id"]].append((_parse_iso(ch["ts"]), ch["to_value"]))
        if not any(agent_role.values()):
            return self._empty_plot("role_distribution", "No data")
        all_ts = sorted({t for series in agent_role.values() for t, _ in series})
        start, end = all_ts[0], all_ts[-1]
        buckets = []
        cur = start
        while cur <= end:
            buckets.append(cur)
            cur = cur.fromtimestamp(cur.timestamp() + 60.0, cur.tzinfo)
        if buckets[-1] < end:
            buckets.append(end)
        counts = {r: [0] * len(buckets) for r in role_ids}
        for aid, series in agent_role.items():
            series.sort()
            j = 0
            cur_r = series[0][1]
            for i, b in enumerate(buckets):
                while j + 1 < len(series) and series[j + 1][0] <= b:
                    j += 1
                    cur_r = series[j][1]
                if cur_r in counts:
                    counts[cur_r][i] += 1
        fig, ax = plt.subplots(figsize=(10, 5))
        for r in role_ids:
            ax.plot(buckets, counts[r], label=r, linewidth=1.2)
        ax.set_title("Role distribution over time")
        ax.set_xlabel("time")
        ax.set_ylabel("agents")
        ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=7)
        fig.autofmt_xdate()
        fig.tight_layout()
        return self._save(fig, "role_distribution")

    # ----- 3. social network -----

    def _render_social_network(self) -> Path:
        msgs = self.store.all_messages_with_conv()
        agents = self.store.list_agents()
        agent_class = {a["agent_id"]: a["class"] for a in agents}
        agent_name = {a["agent_id"]: a["name"] or a["agent_id"] for a in agents}

        edge_weights: dict[tuple[str, str], int] = defaultdict(int)
        for m in msgs:
            sender = m["sender_id"]
            for other in m.get("participants", []):
                if other == sender:
                    continue
                key = (sender, other) if sender < other else (other, sender)
                edge_weights[key] += 1
        g = nx.Graph()
        for a in agents:
            g.add_node(a["agent_id"])
        for (u, v), w in edge_weights.items():
            g.add_edge(u, v, weight=w)
        if g.number_of_nodes() == 0:
            return self._empty_plot("social_network", "No agents")

        deg = dict(g.degree())
        pos = nx.spring_layout(g, seed=42, k=0.6)
        fig, ax = plt.subplots(figsize=(10, 10))
        node_colors = [
            self.class_color.get(agent_class.get(n, ""), (0.5, 0.5, 0.5, 1.0))
            for n in g.nodes
        ]
        node_sizes = [60 + 80 * deg.get(n, 0) for n in g.nodes]
        nx.draw_networkx_nodes(
            g, pos, node_color=node_colors, node_size=node_sizes, ax=ax
        )
        if g.number_of_edges() > 0:
            widths = [max(0.3, 0.4 * g[u][v]["weight"]) for u, v in g.edges]
            nx.draw_networkx_edges(g, pos, width=widths, alpha=0.4, ax=ax)
        labels = {n: agent_name[n] for n in g.nodes}
        nx.draw_networkx_labels(g, pos, labels=labels, font_size=6, ax=ax)
        ax.set_axis_off()
        ax.set_title("Social network (size ∝ degree, edge ∝ messages)")
        return self._save(fig, "social_network")

    # ----- 4. voting timeline -----

    def _render_voting_timeline(self) -> Path:
        proposals = self.store.list_proposals()
        if not proposals:
            return self._empty_plot("voting_timeline", "No votes yet")
        fig, ax = plt.subplots(figsize=(10, 5))
        for p in proposals:
            ts = _parse_iso(p["closed_at"] or p["proposed_at"])
            color = "#2ecc71" if p["outcome"] == "approved" else (
                "#e74c3c" if p["outcome"] == "rejected" else "#999"
            )
            ax.scatter([ts], [p["id"]], color=color, s=120)
            ax.annotate(
                f"{p['change_type']}→{p['to_value']} ({p['target_id']})",
                xy=(ts, p["id"]),
                xytext=(5, 5),
                textcoords="offset points",
                fontsize=7,
            )
        ax.set_title("Voting timeline (green=approved, red=rejected)")
        ax.set_xlabel("time")
        ax.set_ylabel("proposal id")
        fig.autofmt_xdate()
        return self._save(fig, "voting_timeline")

    # ----- 5. mobility heatmap -----

    def _render_mobility_heatmap(self) -> Path:
        changes = self.store.list_status_changes()
        # initial and final class per agent
        initial: dict[str, str] = {}
        final: dict[str, str] = {}
        for ch in sorted(changes, key=lambda c: c["ts"]):
            if ch["change_type"] not in ("class", "initial"):
                continue
            if ch["change_type"] == "initial":
                initial[ch["agent_id"]] = ch["to_value"]
                final[ch["agent_id"]] = ch["to_value"]
            else:
                final[ch["agent_id"]] = ch["to_value"]
        classes = self.cfg.classes
        idx = {c: i for i, c in enumerate(classes)}
        matrix = [[0] * len(classes) for _ in classes]
        for aid, ini in initial.items():
            fin = final.get(aid, ini)
            if ini in idx and fin in idx:
                matrix[idx[ini]][idx[fin]] += 1
        fig, ax = plt.subplots(figsize=(7, 6))
        im = ax.imshow(matrix, cmap="viridis", aspect="auto")
        ax.set_xticks(range(len(classes)))
        ax.set_yticks(range(len(classes)))
        ax.set_xticklabels(classes, rotation=45, ha="right")
        ax.set_yticklabels(classes)
        for i in range(len(classes)):
            for j in range(len(classes)):
                ax.text(j, i, str(matrix[i][j]), ha="center", va="center",
                        color="white" if matrix[i][j] else "#444", fontsize=9)
        ax.set_title("Mobility: initial → final class")
        fig.colorbar(im, ax=ax)
        fig.tight_layout()
        return self._save(fig, "mobility_heatmap")

    # ----- 6. isolation (current social_need) -----

    def _render_isolation(self) -> Path:
        agents = self.store.list_agents()
        if not agents:
            return self._empty_plot("isolation", "No agents")
        agents.sort(key=lambda a: (a.get("class") or "", a.get("social_need") or 0.0))
        names = [a["name"] or a["agent_id"] for a in agents]
        values = [float(a.get("social_need") or 0.0) for a in agents]
        colors = [self.class_color.get(a.get("class"), (0.5, 0.5, 0.5, 1.0)) for a in agents]
        fig, ax = plt.subplots(figsize=(max(8, len(agents) * 0.18), 5))
        ax.bar(range(len(agents)), values, color=colors)
        ax.set_xticks(range(len(agents)))
        ax.set_xticklabels(names, rotation=90, fontsize=6)
        ax.set_ylabel("social need")
        ax.set_title("Isolation (current social need per agent)")
        ax.axhline(self.cfg.social.social_need_critical_threshold,
                   color="red", linestyle="--", linewidth=0.8, label="critical")
        ax.legend(fontsize=8)
        fig.tight_layout()
        return self._save(fig, "isolation")

    # ----- 7. message volume per agent -----

    def _render_message_volume(self) -> Path:
        agents = self.store.list_agents()
        agent_class = {a["agent_id"]: a.get("class") for a in agents}
        agent_name = {a["agent_id"]: a["name"] or a["agent_id"] for a in agents}
        msgs = self.store.all_messages_with_conv()
        counts: dict[str, int] = {a["agent_id"]: 0 for a in agents}
        for m in msgs:
            sid = m["sender_id"]
            if sid in counts:
                counts[sid] += 1
        items = sorted(counts.items(), key=lambda t: t[1], reverse=True)
        labels = [agent_name[i] for i, _ in items]
        values = [v for _, v in items]
        colors = [self.class_color.get(agent_class.get(i), (0.5, 0.5, 0.5, 1.0)) for i, _ in items]
        fig, ax = plt.subplots(figsize=(max(8, len(items) * 0.18), 5))
        ax.bar(range(len(items)), values, color=colors)
        ax.set_xticks(range(len(items)))
        ax.set_xticklabels(labels, rotation=90, fontsize=6)
        ax.set_ylabel("messages sent")
        ax.set_title("Message volume per agent")
        fig.tight_layout()
        return self._save(fig, "message_volume")

    # ----- 8. balances over time -----

    def _render_balances(self) -> Path:
        """Single line chart: mean agent bank balance and the community
        treasury balance over the lifetime of the run, reconstructed by
        replaying the transactions log."""
        # Pull the whole log in chronological order. list_transactions returns
        # newest-first; reverse for replay.
        txs = list(reversed(self.store.list_transactions(limit=1_000_000)))
        agents = self.store.list_agents()
        if not txs and not agents:
            return self._empty_plot("balances", "no transactions yet")

        agent_ids = {a["agent_id"] for a in agents}
        agent_balance: dict[str, float] = {aid: 0.0 for aid in agent_ids}
        community_balance: float = 0.0

        # Bookkeeping rows from initialize() have no party but record the
        # initial balance in `amount`. We can't tell from a single row whether
        # that init applies to community or to an agent, so we look at the
        # `reason` text (the EconomyModule writes "initial community balance"
        # vs "initial balance for <id>") for a hint, and fall back to inferring
        # from the agent id list. If we can't infer, the row is treated as a
        # no-op for the running series — the final samples may then be off,
        # but the SHAPE of the curve (relative movement) is still meaningful.
        def _apply_init(tx: dict) -> None:
            nonlocal community_balance
            reason = (tx.get("reason") or "").lower()
            amount = float(tx.get("amount") or 0)
            if "community" in reason:
                community_balance += amount
                return
            if reason.startswith("initial balance for "):
                aid = tx.get("reason").split("for ", 1)[1].strip()
                if aid in agent_balance:
                    agent_balance[aid] += amount
                return

        ts_series: list[datetime] = []
        mean_series: list[float] = []
        community_series: list[float] = []

        def _record_sample(ts_iso: str | None) -> None:
            if not ts_iso:
                return
            try:
                t = _parse_iso(ts_iso)
            except (TypeError, ValueError):
                return
            mean = (
                sum(agent_balance.values()) / max(1, len(agent_balance))
                if agent_balance
                else 0.0
            )
            ts_series.append(t)
            mean_series.append(mean)
            community_series.append(community_balance)

        for tx in txs:
            kind = tx.get("kind")
            amount = float(tx.get("amount") or 0)
            fp = tx.get("from_party")
            tp = tx.get("to_party")
            if kind == "init" and fp is None and tp is None:
                _apply_init(tx)
            else:
                if fp == "community":
                    community_balance -= amount
                elif fp in agent_balance:
                    agent_balance[fp] -= amount
                if tp == "community":
                    community_balance += amount
                elif tp in agent_balance:
                    agent_balance[tp] += amount
            _record_sample(tx.get("ts"))

        if not ts_series:
            return self._empty_plot("balances", "no transactions yet")

        fig, ax = plt.subplots(figsize=(10, 4.2))
        ax.plot(ts_series, mean_series, label="mean agent balance",
                color="#1f6feb", linewidth=1.6)
        ax.plot(ts_series, community_series, label="community treasury",
                color="#f0883e", linewidth=1.6)
        ax.axhline(0, color="#aaa", linestyle=":", linewidth=0.8)
        ax.set_title("Wealth over time (single chart)")
        ax.set_ylabel("balance (coins)")
        ax.set_xlabel("time")
        ax.legend(loc="best", fontsize=9)
        fig.autofmt_xdate()
        fig.tight_layout()
        return self._save(fig, "balances")

    # ----- helpers -----

    def _empty_plot(self, name: str, text: str) -> Path:
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.text(0.5, 0.5, text, ha="center", va="center", fontsize=12, color="#888")
        ax.set_axis_off()
        return self._save(fig, name)

    def _save(self, fig, name: str) -> Path:
        path = self.out_dir / f"{name}.png"
        fig.savefig(path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        return path
