from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, Sequence

from .query_analyzer import QueryContext, analyze_query
from .schema import MemoryItem, MemoryType


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
    top_k: int,
) -> list[MemoryCandidate]:
    """Retrieve memories relevant to the query."""
    raise NotImplementedError


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
) -> float:
    """Compute flexible-memory utility."""
    raise NotImplementedError


def calculate_memory_budget(
    total_budget: int,
    fixed_tokens: int,
) -> tuple[PipelineStatus, int]:
    """Calculate available memory budget."""
    if total_budget < 0 or fixed_tokens < 0:
        raise ValueError("token budgets must be non-negative")

    if fixed_tokens > total_budget:
        return PipelineStatus.INVALID_BUDGET_CONFIGURATION, 0

    return PipelineStatus.OK, total_budget - fixed_tokens


def select_mandatory(
    protected: Sequence[MemoryItem],
    states: Sequence[MemoryItem],
    memory_budget: int,
) -> MandatorySelectionResult:
    """Select all mandatory memories if they fit."""
    mandatory = list(protected) + list(states)
    used_tokens = sum(memory.token_count for memory in mandatory)

    if used_tokens > memory_budget:
        return MandatorySelectionResult(
            status=PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
            used_tokens=used_tokens,
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
    """Select flexible memories within budget."""
    raise NotImplementedError


def select_memory(
    query: str,
    session_id: str,
    memory_store: MemoryStoreProtocol,
    total_budget: int,
    fixed_tokens: int,
    current_turn: int,
    current_time: datetime,
    *,
    top_k: int = 20,
) -> SelectionResult:
    """Run the memory-selection pipeline."""

    status, memory_budget = calculate_memory_budget(
        total_budget,
        fixed_tokens,
    )

    if status != PipelineStatus.OK:
        return SelectionResult(status=status)

    query_context = analyze_query(
        query=query,
        current_turn=current_turn,
        current_time=current_time,
    )

    retrieved = retrieve_candidates(
        query_context=query_context,
        memory_store=memory_store,
        session_id=session_id,
        top_k=top_k,
    )

    retrieved_ids = tuple(
        candidate.memory.memory_id
        for candidate in retrieved
    )

    expanded = expand_version_chains(
        retrieved,
        memory_store,
    )

    resolved = resolve_temporal_versions(
        expanded,
        query_context,
    )

    states = resolve_required_states(
        resolved,
        query_context,
    )

    protected = retrieve_protected(
        query_context=query_context,
        memory_store=memory_store,
        session_id=session_id,
    )

    mandatory_result = select_mandatory(
        protected,
        states,
        memory_budget,
    )

    if mandatory_result.status != PipelineStatus.OK:
        return SelectionResult(
            status=mandatory_result.status,
            protected=tuple(protected),
            states=tuple(states),
            retrieved_memory_ids=retrieved_ids,
        )

    mandatory_ids = {
        memory.memory_id
        for memory in mandatory_result.memories
    }

    flexible_candidates = [
        candidate
        for candidate in resolved
        if candidate.memory.memory_id not in mandatory_ids
    ]

    for candidate in flexible_candidates:
        candidate.utility_score = compute_utility(
            candidate,
            query_context,
        )

    selected_flexible = select_flexible(
        flexible_candidates,
        mandatory_result.remaining_budget,
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

    selected_ids = tuple(
        memory.memory_id
        for memory in selected_memories
    )

    selected_id_set = set(selected_ids)

    rejected_ids = tuple(
        candidate.memory.memory_id
        for candidate in resolved
        if candidate.memory.memory_id not in selected_id_set
    )

    memory_tokens = sum(
        memory.token_count
        for memory in selected_memories
    )

    return SelectionResult(
        status=PipelineStatus.OK,
        protected=tuple(protected),
        states=tuple(states),
        flexible=flexible_memories,
        retrieved_memory_ids=retrieved_ids,
        selected_memory_ids=selected_ids,
        rejected_memory_ids=rejected_ids,
        memory_tokens=memory_tokens,
        remaining_budget=memory_budget - memory_tokens,
    )