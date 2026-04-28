"""Vector store wrapper.

Two implementations:
  - ChromaVectorStore: real ChromaDB (one persistent collection per run).
  - InMemoryVectorStore: pure-python fallback used when chromadb / a real
    embedding model are not installed. Computes cosine similarity over hashed
    bag-of-words vectors. Coarse but deterministic and dependency-free; lets us
    exercise the rest of the orchestration without GPU/embedding deps.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Protocol


_TOKEN_RE = re.compile(r"[A-Za-z']+")


def _hash_embed(text: str, dim: int = 256) -> list[float]:
    """Deterministic hashed bag-of-words embedding. Cheap and stateless."""
    vec = [0.0] * dim
    for tok in _TOKEN_RE.findall(text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in vec))
    if n > 0:
        vec = [x / n for x in vec]
    return vec


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class VectorStore(Protocol):
    def add(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
    ) -> None: ...
    def query(
        self,
        text: str,
        k: int,
        where: dict | None = None,
    ) -> list[dict]: ...
    def close(self) -> None: ...


@dataclass
class _MemEntry:
    id: str
    document: str
    metadata: dict
    vector: list[float] = field(default_factory=list)


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._items: dict[str, _MemEntry] = {}

    def add(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        for i, doc, md in zip(ids, documents, metadatas):
            self._items[i] = _MemEntry(i, doc, md, _hash_embed(doc))

    def query(self, text: str, k: int, where: dict | None = None) -> list[dict]:
        if not self._items or k <= 0:
            return []
        qv = _hash_embed(text)
        scored: list[tuple[float, _MemEntry]] = []
        for entry in self._items.values():
            if where and not _match_where(entry.metadata, where):
                continue
            scored.append((_cosine(qv, entry.vector), entry))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            {
                "id": e.id,
                "document": e.document,
                "metadata": e.metadata,
                "score": s,
            }
            for s, e in scored[:k]
        ]

    def close(self) -> None:
        self._items.clear()


def _match_where(meta: dict, where: dict) -> bool:
    for k, v in where.items():
        if isinstance(v, dict):
            if "$in" in v and meta.get(k) not in v["$in"]:
                return False
        elif meta.get(k) != v:
            return False
    return True


class ChromaVectorStore:
    """Optional ChromaDB backend. Imported lazily.

    Embedding model selection:
      - If sentence-transformers is installed, use the configured model.
      - Otherwise fall back to chroma's default embedder.
    """

    def __init__(
        self,
        collection_name: str,
        persist_dir: str,
        embedding_model: str | None = None,
    ) -> None:
        import chromadb  # type: ignore

        self._client = chromadb.PersistentClient(path=persist_dir)
        embed_fn = None
        if embedding_model:
            try:
                from chromadb.utils import embedding_functions  # type: ignore

                embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=embedding_model
                )
            except Exception:
                embed_fn = None
        self._collection = self._client.get_or_create_collection(
            collection_name, embedding_function=embed_fn
        )

    def add(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        if not ids:
            return
        self._collection.add(ids=ids, documents=documents, metadatas=metadatas)

    def query(self, text: str, k: int, where: dict | None = None) -> list[dict]:
        if k <= 0:
            return []
        res = self._collection.query(
            query_texts=[text], n_results=k, where=where
        )
        out: list[dict] = []
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for i, doc, md, d in zip(ids, docs, metas, dists):
            out.append(
                {"id": i, "document": doc, "metadata": md or {}, "score": -float(d)}
            )
        return out

    def close(self) -> None:
        # Chroma persistent client flushes on its own.
        pass


def make_vector_store(
    collection_name: str,
    persist_dir: str,
    embedding_model: str | None,
    prefer_chroma: bool = True,
) -> VectorStore:
    """Best-effort factory: try ChromaDB, fall back to in-memory."""
    if prefer_chroma:
        try:
            return ChromaVectorStore(collection_name, persist_dir, embedding_model)
        except Exception:
            pass
    return InMemoryVectorStore()
