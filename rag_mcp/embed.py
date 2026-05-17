from __future__ import annotations

import threading
from typing import List, Sequence


class Embedder:
    """Lazy wrapper around fastembed TextEmbedding + cross-encoder reranker."""

    def __init__(self, model_name: str, reranker_name: str, rerank_enabled: bool):
        self._model_name = model_name
        self._reranker_name = reranker_name
        self._rerank_enabled = rerank_enabled
        self._lock = threading.Lock()
        self._model = None
        self._reranker = None
        self._dim: int | None = None

    @property
    def dimension(self) -> int:
        if self._dim is None:
            self._ensure_model()
            # Probe dim by embedding an empty-ish input.
            vec = next(iter(self._model.passage_embed(["x"])))  # type: ignore
            self._dim = int(len(vec))
        return self._dim

    def _ensure_model(self) -> None:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    self._model = TextEmbedding(model_name=self._model_name)

    def _ensure_reranker(self) -> None:
        if self._reranker is None:
            with self._lock:
                if self._reranker is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    self._reranker = TextCrossEncoder(model_name=self._reranker_name)

    def embed_passages(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        self._ensure_model()
        vecs = list(self._model.passage_embed(list(texts)))  # type: ignore
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> List[float]:
        self._ensure_model()
        vec = next(iter(self._model.query_embed([text])))  # type: ignore
        return vec.tolist()

    def rerank(self, query: str, docs: Sequence[str]) -> List[float]:
        if not self._rerank_enabled or not docs:
            return [0.0] * len(docs)
        self._ensure_reranker()
        scores = list(self._reranker.rerank(query, list(docs)))  # type: ignore
        return [float(s) for s in scores]
