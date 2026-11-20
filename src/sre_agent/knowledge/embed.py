"""Local embeddings with fastembed (ONNX, no API key, no token cost)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

EMBED_MODEL = os.getenv("KB_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIM = 384  # bge-small-en-v1.5
_CACHE = Path(__file__).resolve().parents[3] / ".cache" / "fastembed"


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding  # heavy import; only when embedding

    return TextEmbedding(EMBED_MODEL, cache_dir=str(_CACHE))


def embed_documents(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _model().passage_embed(texts)]


def embed_query(text: str) -> list[float]:
    return next(iter(_model().query_embed(text))).tolist()
