"""ChromaDB-backed vector store. One persistent collection per run."""
from __future__ import annotations


class ChromaVectorStore:
    """ChromaDB backend.

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
