"""Memory manager — short / medium / long term + per-person.

Short-term: kept in-memory by the conversation engine (last N turns verbatim).
Medium-term: persisted summaries; this manager fetches the most recent ones.
Long-term: persisted + embedded into a vector store for semantic retrieval.
Per-person: rolling subjective impression, updated periodically.

The "no forgetting" rule is enforced by never deleting summaries or embeddings.
Context size is controlled by retrieval, not eviction.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..config import MemoryConfig
from ..persistence import RunStore
from .chroma_store import ChromaVectorStore


@dataclass
class RetrievedMemory:
    text: str
    score: float
    metadata: dict


class MemoryManager:
    def __init__(
        self,
        cfg: MemoryConfig,
        store: RunStore,
        run_id: int,
        persist_dir: str,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.run_id = run_id
        self.vector = ChromaVectorStore(
            collection_name=f"run_{run_id}_memory",
            persist_dir=persist_dir,
            embedding_model=cfg.embedding_model,
        )

    async def close(self) -> None:
        await asyncio.to_thread(self.vector.close)

    # ----- writes -----

    async def add_conversation_summary(
        self, agent_id: str, conversation_id: int, summary: str
    ) -> None:
        # SQLite + vector store; both run in worker threads.
        sid = await asyncio.to_thread(
            self.store.add_memory_summary, agent_id, conversation_id, summary
        )
        await asyncio.to_thread(
            self.vector.add,
            [f"summary_{sid}_{agent_id}"],
            [summary],
            [
                {
                    "agent_id": agent_id,
                    "type": "conversation_summary",
                    "conversation_id": conversation_id,
                }
            ],
        )

    async def add_summary(
        self, agent_id: str, summary: str, tick_no: int | None = None
    ) -> None:
        """Persist a reflection summary (not tied to a conversation) + embed it.

        The async messaging model has no conversation to close, so summaries
        come from the periodic per-agent reflection pass. `conversation_id` is
        stored NULL; the vector metadata carries `tick_no` instead.
        """
        sid = await asyncio.to_thread(
            self.store.add_memory_summary, agent_id, None, summary
        )
        await asyncio.to_thread(
            self.vector.add,
            [f"summary_{sid}_{agent_id}"],
            [summary],
            [
                {
                    "agent_id": agent_id,
                    "type": "reflection",
                    "tick_no": tick_no if tick_no is not None else -1,
                }
            ],
        )

    async def add_public_event(
        self, event_id: int, agent_ids: list[str], summary: str
    ) -> None:
        # Mirror the same event into every listener's namespace so we can
        # filter by agent_id at query time.
        ids = [f"event_{event_id}_{aid}" for aid in agent_ids]
        docs = [summary] * len(agent_ids)
        metas = [
            {"agent_id": aid, "type": "public_event", "event_id": event_id}
            for aid in agent_ids
        ]
        await asyncio.to_thread(self.vector.add, ids, docs, metas)

    async def update_person_impression(
        self, observer_id: str, subject_id: str, impression: str
    ) -> None:
        await asyncio.to_thread(
            self.store.upsert_person_memory, observer_id, subject_id, impression
        )

    # ----- reads -----

    async def medium_term(self, agent_id: str) -> list[str]:
        rows = await asyncio.to_thread(
            self.store.recent_summaries, agent_id, self.cfg.medium_term_summaries
        )
        return [r["summary"] for r in rows]

    async def long_term(self, agent_id: str, query: str) -> list[RetrievedMemory]:
        if self.cfg.long_term_retrieval_k <= 0:
            return []
        results = await asyncio.to_thread(
            self.vector.query,
            query,
            self.cfg.long_term_retrieval_k,
            {"agent_id": agent_id},
        )
        return [
            RetrievedMemory(text=r["document"], score=r["score"], metadata=r["metadata"])
            for r in results
        ]

    async def person_impression(
        self, observer_id: str, subject_id: str
    ) -> str | None:
        return await asyncio.to_thread(
            self.store.get_person_memory, observer_id, subject_id
        )
