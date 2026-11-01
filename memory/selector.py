from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Protocol, Sequence

from context.tokenizer import TokenCounter, count_tokens

from .query_analyzer import QueryContext, analyze_query
from .schema import MemoryItem, MemoryType
from .retrieval import Embedder, semantic_retrieve


DEFAULT_UTILITY_WEIGHTS = (0.6, 0.3, 0.1)


class PipelineStatus(str, Enum):
    OK = "ok"
    INVALID_BUDGET_CONFIGURATION = "invalid_budget_configuration"
    INSUFFICIENT_CONTEXT_BUDGET = "insufficient_context_budget"


@dataclass
class MemoryCandidate:
    memory: MemoryItem

    semantic_score: float = 0.0
    keyword_score: float = 0.0
    entity_score: float = 0.0

    relevance_score: float = 0.0
    recency_score: float = 0.0
    utility_score: float = 0.0

    is_applicable: bool = False
    selection_reason: str | None = None


@dataclass(frozen=True)
class MandatorySelectionResult:
    status: PipelineStatus
    memories: tuple[MemoryItem, ...] = ()
    used_tokens: int = 0
    remaining_budget: int = 0


@dataclass(frozen=True)
class SelectionResult:
    status: PipelineStatus

    protected: tuple[MemoryItem, ...] = ()
    states: tuple[MemoryItem, ...] = ()
    flexible: tuple[MemoryItem, ...] = ()

    retrieved_memory_ids: tuple[str, ...] = ()
    selected_memory_ids: tuple[str, ...] = ()
    rejected_memory_ids: tuple[str, ...] = ()

    memory_tokens: int = 0
    remaining_budget: int = 0


class MemoryStoreProtocol(Protocol):
    def find_all_by_key(
        self,
        session_id: str,
        memory_key: str,
    ) -> list[MemoryItem]:
        ...

    def find_active_protected_constraints(
        self,
        session_id: str,
    ) -> list[MemoryItem]:
        ...

    def list_session_memories(
        self,
        session_id: str,
    ) -> list[MemoryItem]:
        ...


def retrieve_candidates(
    query_context: QueryContext,
    memory_store: MemoryStoreProtocol,
    session_id: str,
    *,
    embedder: Embedder,
    top_k: int,
) -> list[MemoryCandidate]:
    """Retrieve memories relevant to the query."""
    if not isinstance(query_context, QueryContext):
        raise TypeError("query_context must be QueryContext")
    
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")
    
    if not callable(embedder):
        raise TypeError("embedder must be callable")
    
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    
    memories = memory_store.list_session_memories(session_id)
    
    available: list[MemoryItem] = []
    
    for memory in memories:
        if memory.turn_id > query_context.current_turn:
            continue
        
        if memory.memory_type == MemoryType.CONSTRAINT and memory.is_protected and memory.is_active:
            continue
        
        available.append(memory)
        
    hits = semantic_retrieve(
        query=query_context.query,
        memories=available,
        embedder=embedder,
        top_k=top_k,
    )
    
    return [
        MemoryCandidate(
            memory=hit.memory,
            semantic_score=hit.score,
            relevance_score=max(hit.score, 0.0)
        )
        for hit in hits
    ]


def expand_version_chains(
    candidates: Sequence[MemoryCandidate],
    memory_store: MemoryStoreProtocol,
) -> list[MemoryCandidate]:
    """Expand versioned candidates by memory_key."""
    raise NotImplementedError


def resolve_temporal_versions(
    candidates: Sequence[MemoryCandidate],
    query_context: QueryContext,
) -> list[MemoryCandidate]:
    """Resolve candidates to the correct temporal version."""
    raise NotImplementedError


def is_constraint_applicable(
    constraint: MemoryItem,
    query_context: QueryContext,
) -> bool:
    """Return whether a constraint applies to the current task."""
    raise NotImplementedError


def retrieve_protected(
    query_context: QueryContext,
    memory_store: MemoryStoreProtocol,
    session_id: str,
) -> list[MemoryItem]:
    """Retrieve applicable active protected constraints."""
    protected: list[MemoryItem] = []

    for memory in memory_store.find_active_protected_constraints(session_id):
        if memory.memory_type != MemoryType.CONSTRAINT:
            continue
        if not memory.is_active or not memory.is_protected:
            continue
        if is_constraint_applicable(memory, query_context):
            protected.append(memory)

    return protected


def resolve_required_states(
    candidates: Sequence[MemoryCandidate],
    query_context: QueryContext,
) -> list[MemoryItem]:
    """Select states required by the current task."""
    raise NotImplementedError


def compute_utility(
    candidate: MemoryCandidate,
    query_context: QueryContext,
    *,
    weights: tuple[float, float, float] = DEFAULT_UTILITY_WEIGHTS,
) -> float:
    if not isinstance(candidate, MemoryCandidate):
        raise TypeError("candidate must be MemoryCandidate")
    
    if not isinstance(query_context, QueryContext):
        raise TypeError("query_context must be QueryContext")
    
    if len(weights) != 3:
        raise ValueError("weights must contain exactly three values")
    
    if any(
        isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0
        for weight in weights
    ):
        raise ValueError("utility weights must be non-negative numbers")
    
    if sum(weights) <= 0:
        raise ValueError("utility weights must have a positive sum")
    
    (relevance_weight, importance_weight, recency_weight) = weights
    
    relevance = max(0.0, min(1.0, candidate.relevance_score))
    
    importance = candidate.memory.importance
    
    turn_distance = max(query_context.current_turn - candidate.memory.turn_id, 0)
    
    recency = 1.0 / (1.0 + turn_distance)
    
    candidate.relevance_score = relevance
    candidate.recency_score = recency
    
    utility = relevance_weight * relevance + importance_weight * importance + recency_weight * recency
    
    return utility


def calculate_memory_budget(
    total_budget: int,
    fixed_tokens: int,
) -> tuple[PipelineStatus, int]:
    """Calculate available memory budget."""
    if type(total_budget) is not int or total_budget < 0:
        raise ValueError("token_budget must be a non-negative integer")

    if type(fixed_tokens) is not int or fixed_tokens < 0:
        raise ValueError("fixed tokens must be a non-negative integer")

    if fixed_tokens > total_budget:
        return (PipelineStatus.INVALID_BUDGET_CONFIGURATION, 0)

    return (PipelineStatus.OK, total_budget - fixed_tokens)


def select_mandatory(
    protected: Sequence[MemoryItem],
    states: Sequence[MemoryItem],
    memory_budget: int,
) -> MandatorySelectionResult:
    """Select all mandatory memories if they fit."""
    if type(memory_budget) is not int or memory_budget < 0:
        raise ValueError("memory_budget must be a non-negative integer")

    mandatory: list[MemoryItem] = []
    seen: set[str] = set()
    
    for memory in list(protected) + list(states):
        if not isinstance(memory, MemoryItem):
            raise TypeError("mandatory memories must be MemoryItem objects")
        
        if memory.memory_id in seen:
            continue
        
        seen.add(memory.memory_id)
        mandatory.append(memory)
    
    used_tokens = sum(memory.token_count for memory in mandatory)

    if used_tokens > memory_budget:
        return MandatorySelectionResult(
            status=PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
            memories=tuple(mandatory),
            used_tokens=used_tokens,
            remaining_budget=0,
        )

    return MandatorySelectionResult(
        status=PipelineStatus.OK,
        memories=tuple(mandatory),
        used_tokens=used_tokens,
        remaining_budget=memory_budget - used_tokens,
    )


def select_flexible(
    candidates: Sequence[MemoryCandidate],
    budget: int,
) -> list[MemoryCandidate]:
    if type(budget) is not int or budget < 0:
        raise ValueError("budget must be a non-negative integer")
    
    if any(
        not isinstance(candidate, MemoryCandidate)
        for candidate in candidates
    ):
        raise TypeError("candidates must contain MemoryCandidate objects")
    
    def score(candidate: MemoryCandidate) -> float:
        return candidate.utility_score / max(candidate.memory.token_count, 1)
        
    ranked = sorted(
        candidates,
        key=lambda candidate:
            candidate.memory.memory_id,
    )
    
    ranked.sort(
        key=lambda candidate: (
            score(candidate),
            candidate.utility_score,
            candidate.relevance_score,
            candidate.recency_score,
        ),
        reverse=True,
    )
    
    selected: list[MemoryCandidate] = []
    remaining_budget = budget
    
    for candidate in ranked:
        token_cost = candidate.memory.token_count
        
        if token_cost > remaining_budget:
            candidate.selection_reason = "rejected_budget"
            continue
        
        candidate.selection_reason = "selected_flexible"
        
        selected.append(candidate)
        remaining_budget -= token_cost
        
    return selected


def select_memory(
    query: str,
    session_id: str,
    memory_store: MemoryStoreProtocol,
    total_budget: int,
    fixed_tokens: int,
    current_turn: int,
    current_time: datetime,
    *,
    embedder: Embedder,
    token_counter: TokenCounter | None = None,
    top_k: int = 20,
    utility_weights: tuple[float, float, float] = DEFAULT_UTILITY_WEIGHTS,
) -> SelectionResult:
    """Run the memory-selection pipeline."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")

    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")
    
    if type(current_turn) is not int or current_turn < 0:
        raise ValueError("current_turn must be a non-negative integer")
    
    if not isinstance(current_time, datetime):
        raise TypeError("current_time must be a datetime")
    
    if not callable(embedder):
        raise TypeError("embedder must be callable")

    if token_counter is not None and not callable(token_counter):
        raise TypeError("token_counter must be callable")
    
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")

    status, memory_budget = calculate_memory_budget(
        total_budget=total_budget,
        fixed_tokens=fixed_tokens,
    )

    if status != PipelineStatus.OK:
        return SelectionResult(
            status=status,
            remaining_budget=0,
        )

    query_context = analyze_query(
        query=query,
        current_turn=current_turn,
        current_time=current_time,
    )

    retrieved = retrieve_candidates(
        query_context=query_context,
        memory_store=memory_store,
        session_id=session_id,
        embedder=embedder,
        top_k=top_k,
    )

    retrieved_ids = tuple(
        candidate.memory.memory_id
        for candidate in retrieved
    )

    expanded = expand_version_chains(
        candidates=retrieved,
        memory_store=memory_store,
    )

    resolved = resolve_temporal_versions(
        candidates=expanded,
        query_context=query_context,
    )
    
    resolved = _retokenize_candidates(
        resolved,
        token_counter,
    )

    states = resolve_required_states(
        candidates=resolved,
        query_context=query_context,
    )

    states = [
        _with_token_count(
            memory,
            token_counter,
        )
        for memory in states
    ]

    protected = retrieve_protected(
        query_context=query_context,
        memory_store=memory_store,
        session_id=session_id,
    )
    
    protected = [
        _with_token_count(
            memory,
            token_counter,
        )
        for memory in protected
    ]

    mandatory_result = select_mandatory(
        protected=protected,
        states=states,
        memory_budget=memory_budget,
    )

    if mandatory_result.status != PipelineStatus.OK:
        mandatory = tuple(protected) + tuple(states)
        
        return SelectionResult(
            status=mandatory_result.status,
            protected=tuple(protected),
            states=tuple(states),
            retrieved_memory_ids=retrieved_ids,
            selected_memory_ids=tuple(memory.memory_id for memory in mandatory),
            memory_tokens=mandatory_result.used_tokens,
            remaining_budget=0,
        )

    mandatory_ids = {
        memory.memory_id
        for memory in mandatory_result.memories
    }
    
    flexible_candidates: list[MemoryCandidate] = []

    for candidate in resolved:
        if candidate.memory.memory_id in mandatory_ids:
            continue
        
        if candidate.memory.memory_type == MemoryType.CONSTRAINT and candidate.memory.is_active and candidate.memory.is_protected:
            continue
        
        candidate.utility_score = compute_utility(
            candidate,
            query_context,
            weights=utility_weights,
        )
        
        flexible_candidates.append(candidate)

    selected_flexible = select_flexible(
        candidates=flexible_candidates,
        budget=mandatory_result.remaining_budget,
    )

    flexible_memories = tuple(
        candidate.memory
        for candidate in selected_flexible
    )

    selected_memories = (
        tuple(protected)
        + tuple(states)
        + flexible_memories
    )
    
    unique_selected: list[MemoryItem] = []
    
    seen_ids: set[str] = set()

    for memory in selected_memories:
        if memory.memory_id in seen_ids:
            continue
        
        seen_ids.add(memory.memory_id)
        unique_selected.append(memory)
        
    selected_ids = tuple(memory.memory_id for memory in unique_selected)

    selected_id_set = set(selected_ids)
    
    rejected_ids: list[str] = []
    rejected_seen: set[str] = set()

    for candidate in resolved:
        memory_id = candidate.memory.memory_id
        if memory_id not in selected_id_set and memory_id not in rejected_seen:
            rejected_seen.add(candidate.memory.memory_id)
            rejected_ids.append(candidate.memory.memory_id)

    memory_tokens = sum(
        memory.token_count
        for memory in unique_selected
    )
    
    remaining_budget = max(memory_budget - memory_tokens, 0)

    return SelectionResult(
        status=PipelineStatus.OK,
        protected=tuple(protected),
        states=tuple(states),
        flexible=flexible_memories,
        retrieved_memory_ids=retrieved_ids,
        selected_memory_ids=selected_ids,
        rejected_memory_ids=tuple(rejected_ids),
        memory_tokens=memory_tokens,
        remaining_budget=remaining_budget,
    )
    
    
def _with_token_count(
    memory: MemoryItem,
    token_counter: TokenCounter | None,
) -> MemoryItem:
    if token_counter is None:
        return memory

    return replace(
        memory,
        token_count=count_tokens(
            memory.content,
            token_counter,
        ),
    )
    
    
def _retokenize_candidates(
    candidates: Sequence[MemoryCandidate],
    token_counter: TokenCounter | None,
) -> list[MemoryCandidate]:
    if token_counter is None:
        return list(candidates)
    
    result: list[MemoryCandidate] = []
    
    for candidate in candidates:
        candidate.memory = _with_token_count(
            candidate.memory,
            token_counter,
        )
        result.append(candidate)

    return result