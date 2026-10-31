from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math

from .schema import MemoryItem


Embedder = Callable[[str], Sequence[float]]


@dataclass(frozen=True)
class RetrievalHit:
    memory: MemoryItem
    score: float
    
    
def embed_vector(
    text: str,
    embedder: Embedder,
) -> tuple[float, ...]:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    
    if not text.strip():
        raise ValueError("text must be non-empty")
    
    if not callable(embedder):
        raise TypeError("embedder must be callable")
    
    raw_vector = embedder(text)
    
    try:
        vector = tuple(raw_vector)
    except TypeError as exc:
        raise TypeError("embber must return a sequence of numbers") from exc
    
    if not vector:
        raise ValueError("embedding vector must not be empty")
    
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("embedding values must be finite numbers")
        
    return tuple(
        float(value)
        for value in vector
    )
    
    
def cosine_similarity(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    left_vector = tuple(left)
    right_vector = tuple(right)
    
    if not left_vector or not right_vector:
        raise ValueError("embedding vectors must not be empty")
    
    if len(left_vector) != len(right_vector):
        raise ValueError("embedding dimensions must match")
    
    for value in left_vector + right_vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("embedding values must be finite numbers")
        
    dot_product = sum(
        left_value * right_value
        for left_value, right_value in zip(left_vector, right_vector)
    )
    
    left_norm = math.sqrt(
        sum(
            value * value
            for value in left_vector
        )
    )
    
    right_norm = math.sqrt(
        sum(
            value * value
            for value in right_vector
        )
    )
    
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    
    score = dot_product / (left_norm * right_norm)
    
    return max(-1.0, min(1.0, score))


def semantic_retrieve(
    query: str,
    memories: Sequence[MemoryItem],
    *,
    embedder: Embedder,
    top_k: int = 20,
) -> list[RetrievalHit]:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    
    if not query.strip():
        raise ValueError("query must be non-empty")
    
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    
    if not memories:
        return []
    
    if any(
        not isinstance(memory, MemoryItem)
        for memory in memories
    ):
        raise TypeError("memories must contain MemoryItem objects")
    
    query_vector = embed_vector(query, embedder)
    
    hits: list[RetrievalHit] = []
    
    for memory in memories:
        memory_vector = embed_vector(memory.content, embedder)
        
        score = cosine_similarity(query_vector, memory_vector)
        
        hits.append(
            RetrievalHit(
                memory=memory,
                score=score,
            )
        )
        
    hits.sort(
        key=lambda hit: (
            -hit.score,
            hit.memory.memory_id,
        )
    )
    
    return hits[:top_k]