"""공통 SelectionResult를 반환하는 토큰 예산 기반 baseline 구현.

제약과 상태를 포함해 선택된 모든 기억을 유지 우선순위 내림차순으로
``flexible``에 넣는다. 버전을 해석하거나 보호 기억을 우선 보존하지 않는다.
Sliding Window는 대화 원문을 자르지 않고 기억 스냅샷의 항목 단위로 선택한다.

벡터 기반 전략에는 embedding 함수를 명시적으로 전달해야 한다. 키워드 검색이나
fixture로 대체하지 않으며, 두 전략에 동일한 embedding 모델 함수를 사용한다.
공통 TokenCounter를 전달하지 않으면 MemoryItem.token_count를 비용으로 사용한다.
최종 형식이 반영된 전체 입력의 토큰 측정과 예산 검증은 Context Builder가 수행한다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
import math

from context.tokenizer import TokenCounter, count_tokens

from .schema import MemoryItem
from .selector import PipelineStatus, SelectionResult


Embedder = Callable[[str], Sequence[float]]


def _nonnegative_int(name: str, value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _prepare(
    memories: Sequence[MemoryItem],
    session_id: str,
    total_budget: int,
    fixed_tokens: int,
    current_turn: int | None,
    token_counter: TokenCounter | None,
) -> tuple[list[MemoryItem], int]:
    _nonnegative_int("total_budget", total_budget)
    _nonnegative_int("fixed_tokens", fixed_tokens)
    if current_turn is not None:
        _nonnegative_int("current_turn", current_turn)
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")
    available: list[MemoryItem] = []
    seen: set[str] = set()
    for memory in memories:
        if memory.session_id != session_id:
            continue
        if current_turn is not None and memory.turn_id > current_turn:
            continue
        if memory.memory_id in seen:
            raise ValueError(f"duplicate memory_id: {memory.memory_id}")
        seen.add(memory.memory_id)
        if token_counter is not None:
            memory = replace(memory, token_count=count_tokens(memory.content, token_counter))
        available.append(memory)
    return available, total_budget - fixed_tokens


def _pack(
    ranked: Sequence[MemoryItem],
    budget: int,
    *,
    retrieved: tuple[str, ...] = (),
    contiguous: bool = False,
) -> SelectionResult:
    if budget < 0:
        return SelectionResult(status=PipelineStatus.INVALID_BUDGET_CONFIGURATION)
    selected: list[MemoryItem] = []
    rejected: list[str] = []
    remaining = budget
    stopped = False
    for memory in ranked:
        if stopped or memory.token_count > remaining:
            rejected.append(memory.memory_id)
            stopped = contiguous
            continue
        selected.append(memory)
        remaining -= memory.token_count
    return SelectionResult(
        status=PipelineStatus.OK,
        flexible=tuple(selected),
        retrieved_memory_ids=retrieved,
        selected_memory_ids=tuple(memory.memory_id for memory in selected),
        rejected_memory_ids=tuple(rejected),
        memory_tokens=budget - remaining,
        remaining_budget=remaining,
    )


def sliding_window(
    memories: Sequence[MemoryItem],
    *,
    session_id: str,
    total_budget: int,
    fixed_tokens: int,
    current_turn: int | None = None,
    token_counter: TokenCounter | None = None,
) -> SelectionResult:
    """최근 turn부터 기억을 항목 단위로 연속해서 선택한다.

    같은 turn에서는 생성 시각이 늦은 기억을 우선하고, 동률이면 memory_id
    오름차순으로 정렬한다. 처음으로 예산에 들어가지 않는 항목을 만나면
    더 오래된 작은 항목이 들어갈 수 있더라도 선택을 중단한다.
    """
    available, budget = _prepare(
        memories, session_id, total_budget, fixed_tokens, current_turn, token_counter,
    )
    if budget < 0:
        return _pack((), budget)
    # 안정 정렬을 순차 적용해 날짜값을 뒤집지 않고 동률 순서를 일정하게 유지한다.
    available.sort(key=lambda memory: memory.memory_id)
    available.sort(key=lambda memory: memory.created_at, reverse=True)
    available.sort(key=lambda memory: memory.turn_id, reverse=True)
    return _pack(available, budget, contiguous=True)


def _vector(text: str, embedder: Embedder) -> tuple[float, ...]:
    values = tuple(embedder(text))
    if not values or any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) for value in values
    ):
        raise ValueError("embeddings must be non-empty finite numeric vectors")
    norm = math.hypot(*values)
    if not math.isfinite(norm):
        raise ValueError("embedding norm must be finite")
    # 영벡터는 모든 벡터와의 유사도를 0으로 처리한다.
    return tuple(value / norm for value in values) if norm else values


def _retrieve(
    query: str,
    memories: Sequence[MemoryItem],
    embedder: Embedder,
    top_k: int,
) -> list[tuple[MemoryItem, float]]:
    if not memories:
        return []
    query_vector = _vector(query, embedder)
    scored: list[tuple[MemoryItem, float]] = []
    for memory in memories:
        vector = _vector(memory.content, embedder)
        if len(vector) != len(query_vector):
            raise ValueError("query and memory embedding dimensions must match")
        score = sum(left * right for left, right in zip(query_vector, vector))
        scored.append((memory, max(-1.0, min(1.0, score))))
    scored.sort(key=lambda item: (-item[1], item[0].memory_id))
    return scored[:top_k]


def _validate_retrieval(query: str, top_k: int) -> None:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")


def vector_top_k(
    query: str,
    memories: Sequence[MemoryItem],
    *,
    embedder: Embedder,
    session_id: str,
    total_budget: int,
    fixed_tokens: int,
    top_k: int = 20,
    current_turn: int | None = None,
    token_counter: TokenCounter | None = None,
) -> SelectionResult:
    """코사인 유사도 상위 K개를 검색한 뒤 유사도 순으로 예산 내에서 선택한다.

    예산보다 큰 항목은 건너뛴다. 검색 ID에는 예산 때문에 제외한 항목도 포함하며,
    최초 Top-K 순서를 유지한다. 유사도가 같으면 memory_id 오름차순으로 정렬한다.
    """
    _validate_retrieval(query, top_k)
    available, budget = _prepare(
        memories, session_id, total_budget, fixed_tokens, current_turn, token_counter,
    )
    if budget < 0:
        return _pack((), budget)
    retrieved = _retrieve(query, available, embedder, top_k)
    return _pack(
        [memory for memory, _ in retrieved], budget,
        retrieved=tuple(memory.memory_id for memory, _ in retrieved),
    )


def utility_per_token(
    query: str,
    memories: Sequence[MemoryItem],
    *,
    embedder: Embedder,
    session_id: str,
    total_budget: int,
    fixed_tokens: int,
    current_turn: int,
    top_k: int = 20,
    weights: tuple[float, float, float] = (0.6, 0.3, 0.1),
    token_counter: TokenCounter | None = None,
) -> SelectionResult:
    """동일한 코사인 Top-K 후보를 utility / max(token_count, 1) 순으로 정렬한다.

    효용 = w_relevance * max(cosine, 0) + w_importance * importance
           + w_recency / (1 + current_turn - turn_id).
    기본 가중치는 초기 정책값이며 실험으로 학습한 결과가 아니다.
    가중치는 유한한 0 이상 값이어야 하고 합은 양수여야 한다. 동률이면
    최초 검색 순서를 따른다. 토큰 수가 0인 기억은 예산을 소비하지 않지만
    순위 계산에서는 분모를 1로 처리한다. 버전 확장이나 보호 검색은 수행하지 않는다.
    """
    _validate_retrieval(query, top_k)
    _nonnegative_int("current_turn", current_turn)
    if len(weights) != 3 or any(
        isinstance(weight, bool) or not isinstance(weight, (int, float))
        or not math.isfinite(weight) or weight < 0 for weight in weights
    ) or not math.isfinite(sum(weights)) or sum(weights) <= 0:
        raise ValueError("weights must contain three finite non-negative values with positive sum")
    available, budget = _prepare(
        memories, session_id, total_budget, fixed_tokens, current_turn, token_counter,
    )
    if budget < 0:
        return _pack((), budget)
    retrieved = _retrieve(query, available, embedder, top_k)
    relevance_weight, importance_weight, recency_weight = weights

    def priority(item: tuple[MemoryItem, float]) -> float:
        memory, similarity = item
        utility = (
            relevance_weight * max(similarity, 0.0)
            + importance_weight * memory.importance
            + recency_weight / (1 + current_turn - memory.turn_id)
        )
        return utility / max(memory.token_count, 1)

    ranked = sorted(retrieved, key=priority, reverse=True)
    return _pack(
        [memory for memory, _ in ranked], budget,
        retrieved=tuple(memory.memory_id for memory, _ in retrieved),
    )
