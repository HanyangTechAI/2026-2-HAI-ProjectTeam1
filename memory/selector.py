from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import re
import logging
from typing import Protocol, Sequence

from context.tokenizer import TokenCounter, count_tokens

from .query_analyzer import (
    QueryContext, TemporalIntent, analyze_query, _detect_tools,
    _concepts, _specific_entities, _recipients, _actions,
)
from .schema import MemoryItem, MemoryType
from .retrieval import Embedder, semantic_retrieve
from .store import MemoryStoreConsistencyError
from .ablation import FULL_ABLATION, SelectorAblationConfig


DEFAULT_UTILITY_WEIGHTS = (0.6, 0.3, 0.1)
_LOGGER = logging.getLogger(__name__)


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
    exclude_active_protected: bool = True,
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
    
    if type(exclude_active_protected) is not bool:
        raise TypeError("exclude_active_protected must be bool")
    
    memories = memory_store.list_session_memories(session_id)
    
    available: list[MemoryItem] = []
    
    for memory in memories:
        if memory.turn_id > query_context.current_turn:
            continue
        
        if exclude_active_protected and memory.memory_type == MemoryType.CONSTRAINT and memory.is_protected and memory.is_active:
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
    expanded = {}
    for candidate in candidates:
        expanded.setdefault((candidate.memory.session_id, candidate.memory.memory_id), candidate)
    visited = set()
    for candidate in candidates:
        memory = candidate.memory
        if memory.memory_key is None:
            continue
        key = (memory.session_id, memory.memory_key)
        if key in visited:
            continue
        visited.add(key)
        for version in memory_store.find_all_by_key(*key):
            if (version.session_id, version.memory_key) != key:
                raise ValueError("version chain contains a different session or key")
            identity = (version.session_id, version.memory_id)
            # 새 버전에 검색 점수를 임의로 복사하지 않는다.
            expanded.setdefault(identity, MemoryCandidate(memory=version))
    return list(expanded.values())


def resolve_temporal_versions(
    candidates: Sequence[MemoryCandidate],
    query_context: QueryContext,
) -> list[MemoryCandidate]:
    """요청 시점 또는 기간에 맞는 버전을 선택한다.

    기간 질의의 시간 관련성은 요청 기간과 유효 기간의 겹치는 길이로
    계산한다. 길이가 같으면 valid_from이 늦은 버전을 우선하고, 그것도
    같으면 memory_id 오름차순으로 선택해 입력 순서에 영향을 받지 않는다.
    이는 프로젝트의 구현 정책이며 문서에 명시된 계산 공식은 아니다.
    현재 ACTIVE 중복이나 단일 시점의 중복 유효 버전은 오류로 처리한다.
    """
    historical = query_context.temporal_intent in (
        TemporalIntent.HISTORICAL, TemporalIntent.RELATIVE_TIME,
    )
    target = query_context.target_time
    period = query_context.target_range
    if historical and target is None and period is None:
        raise ValueError("historical queries require target_time or target_range")
    if period is not None and period[0] >= period[1]:
        raise ValueError("target_range must have an increasing start and end")
    groups = {}
    resolved = []
    seen = set()
    for candidate in candidates:
        memory = candidate.memory
        identity = (memory.session_id, memory.memory_id)
        if identity in seen or memory.turn_id > query_context.current_turn:
            continue
        seen.add(identity)
        if memory.memory_key is None:
            resolved.append(candidate)
        else:
            groups.setdefault((memory.session_id, memory.memory_key), []).append(candidate)
    for group in groups.values():
        valid = []
        for candidate in group:
            memory = candidate.memory
            if historical:
                times = (target,) if target is not None else period
                if any((time.utcoffset() is None) != (memory.valid_from.utcoffset() is None)
                       for time in times):
                    raise ValueError("query and memory timestamps must use compatible timezones")
            if not historical:
                matches = memory.is_active
            elif target is not None:
                matches = memory.valid_from <= target and (memory.valid_to is None or target < memory.valid_to)
            else:
                start, end = period
                overlap_start = max(start, memory.valid_from)
                overlap_end = min(end, memory.valid_to) if memory.valid_to is not None else end
                matches = overlap_start < overlap_end
            if matches:
                valid.append(candidate)
        if len(valid) > 1:
            if not historical or target is not None:
                raise MemoryStoreConsistencyError("multiple active or point-in-time valid versions for one key")
            start, end = period

            def temporal_rank(candidate: MemoryCandidate):
                memory = candidate.memory
                overlap_start = max(start, memory.valid_from)
                overlap_end = min(end, memory.valid_to) if memory.valid_to is not None else end
                return (overlap_end - overlap_start, memory.valid_from)

            # max는 동점에서 첫 항목을 유지하므로 ID를 먼저 정렬한다.
            chosen = max(sorted(valid, key=lambda item: item.memory.memory_id), key=temporal_rank)
            valid = [chosen]
        if not valid:
            _LOGGER.info("no_valid_version session=%s key=%s", group[0].memory.session_id, group[0].memory.memory_key)
        for candidate in valid:
            candidate.selection_reason = "temporal_range_overlap" if historical and target is None else "temporal_version"
        resolved.extend(valid)
    return resolved


def is_constraint_applicable(
    constraint: MemoryItem,
    query_context: QueryContext,
) -> bool:
    """Return whether a constraint applies to the current task."""
    if constraint.memory_type != MemoryType.CONSTRAINT:
        return False
    # 과거 규칙 자체를 묻는 질문은 일반 검색 경로로 처리한다.
    if re.search(r"규칙|제약|정책|\b(?:rule|policy|constraint)\b", query_context.query.casefold()) and not re.search(
        r"보내줘|발송해|삭제해|지워줘|만들어|변경해|처리해|\b(?:send|delete|create|update)\b",
        query_context.query.casefold(),
    ):
        return False
    text = f"{constraint.memory_key or ''} {constraint.content}".casefold()
    if ("외부" in text and "내부" in query_context.query) or ("내부" in text and "외부" in query_context.query):
        return False
    tools = set(_detect_tools(text))
    requested = set(query_context.required_tools) | set(_detect_tools(query_context.query))
    if tools and not tools.intersection(requested):
        return False
    targets = set(_recipients(constraint.content))
    requested_targets = set(_recipients(query_context.query)) | set(query_context.entities)
    if targets and not targets.intersection(requested_targets):
        return False
    filenames = set(re.findall(r"[\w.-]+\.(?:pdf|docx?|txt|csv|xlsx?)\b", constraint.content, re.ASCII | re.I))
    if filenames and not any(name.casefold() in query_context.query.casefold() for name in filenames):
        return False
    constrained_actions = set(_constraint_actions(text))
    if constrained_actions and not constrained_actions.intersection(_actions(query_context.query)) and "처리해" not in query_context.query:
        return False
    return bool(tools.intersection(requested)) or any(
        entity.casefold() in text for entity in query_context.entities
    )


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



def _constraint_actions(text: str) -> tuple[str, ...]:
    actions = _actions(text)
    # algorithm.md의 축약 예시: '외부 이메일은 승인 필요'.
    if not actions and "email" in _detect_tools(text) and ("승인" in text or "approval" in text):
        return ("send",)
    return actions




def resolve_required_states(
    candidates: Sequence[MemoryCandidate],
    query_context: QueryContext,
    *,
    allow_multiple_versions: bool = False,
) -> list[MemoryItem]:
    """Select required states; temporal ablation preserves retrieved versions as-is."""
    states = []
    seen = set()
    for candidate in candidates:
        memory = candidate.memory
        if memory.memory_type != MemoryType.STATE:
            continue
        text = f"{memory.memory_key or ''} {memory.content}".casefold()
        if memory.turn_id > query_context.current_turn:
            continue
        requested = set(_concepts(query_context.query))
        available = set(_concepts(text))
        # '파일' 하나가 맞는다고 모든 파일을 필수로 올리지 않는다.
        specific_concepts = requested - {"파일", "문서", "이메일"}
        requested_names = set(_specific_entities(query_context.query)) - set(_recipients(query_context.query))
        name_match = any(name.casefold() in text for name in requested_names)
        if requested_names:
            relevant = name_match
        elif specific_concepts:
            relevant = bool(specific_concepts & available)
            # 보고서를 보낼 수신자 정보도 해당 행동의 필수 State이다.
            relevant = relevant or ("send" in _actions(query_context.query)
                                    and "담당자" in available
                                    and ("이메일" in available or "@" in memory.content))
        else:
            relevant = bool(requested & available)
            if not requested:
                relevant = any(entity.casefold() in text for entity in query_context.entities)
        if not relevant:
            continue
        key = (memory.session_id, memory.memory_key)
        if key in seen and not allow_multiple_versions:
            raise MemoryStoreConsistencyError("required states contain multiple versions for one key")
        seen.add(key)
        candidate.is_applicable = True
        candidate.selection_reason = "required_state_entity_match"
        states.append(memory)
    return states


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
    *,
    token_aware: bool = True,
) -> list[MemoryCandidate]:
    if type(budget) is not int or budget < 0:
        raise ValueError("budget must be a non-negative integer")
    
    if type(token_aware) is not bool:
        raise TypeError("token_aware must be bool")
    
    if any(
        not isinstance(candidate, MemoryCandidate)
        for candidate in candidates
    ):
        raise TypeError("candidates must contain MemoryCandidate objects")
    
    def score(candidate: MemoryCandidate) -> float:
        if token_aware:
            return candidate.utility_score / max(candidate.memory.token_count, 1)
        
        return candidate.utility_score
        
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
    top_k: int = 5,
    utility_weights: tuple[float, float, float] = DEFAULT_UTILITY_WEIGHTS,
    ablation: SelectorAblationConfig = FULL_ABLATION,
) -> SelectionResult:
    
    # =========================================================
    # 0. Input validation
    # =========================================================
    
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
    
    if not isinstance(ablation, SelectorAblationConfig):
        raise TypeError("ablation must be SelectorAblationConfig")

    # =========================================================
    # 1. Calculate memory budget
    #
    # B_memory = B_total - C_fixed
    # =========================================================

    status, memory_budget = calculate_memory_budget(
        total_budget=total_budget,
        fixed_tokens=fixed_tokens,
    )

    if status != PipelineStatus.OK:
        return SelectionResult(
            status=status,
            remaining_budget=0,
        )
        
    # =========================================================
    # 2. Query analysis
    # =========================================================

    query_context = analyze_query(
        query=query,
        current_turn=current_turn,
        current_time=current_time,
    )
    
    # =========================================================
    # 3. General retrieval
    #
    # Protected active constraints are excluded here.
    # They use the separate protected retrieval path.
    # =========================================================

    retrieved = retrieve_candidates(
        query_context=query_context,
        memory_store=memory_store,
        session_id=session_id,
        embedder=embedder,
        top_k=top_k,
        exclude_active_protected=ablation.constraint_preservation,
    )

    retrieved_ids = tuple(
        candidate.memory.memory_id
        for candidate in retrieved
    )

    # =========================================================
    # 4. Version chain expansion
    # =========================================================

    if ablation.temporal_resolution:
        expanded = expand_version_chains(
            candidates=retrieved,
            memory_store=memory_store,
        )
        
        # =========================================================
        # 5. Temporal resolution
        #
        # CURRENT       -> active version
        # HISTORICAL    -> valid version at requested time
        # RELATIVE_TIME -> resolved historical version
        # =========================================================

        resolved = resolve_temporal_versions(
            candidates=expanded,
            query_context=query_context,
        )
        
    else:
        resolved = list(retrieved)
    
    resolved = _retokenize_candidates(
        resolved,
        token_counter,
    )
    
    # =========================================================
    # 6. Required State
    # =========================================================

    if ablation.mandatory_state:
        states = resolve_required_states(
            candidates=resolved,
            query_context=query_context,
            allow_multiple_versions=not ablation.temporal_resolution,
        )

        states = _retokenize_memories(
            states,
            token_counter,
        )
        
    else:
        states = []
    
    # =========================================================
    # 7. Protected Constraint
    #
    # General retrieval과 별도 경로.
    # Semantic relevance 경쟁 없이 applicable한 active
    # protected constraint를 확보한다.
    # =========================================================

    if ablation.constraint_preservation:
        protected = retrieve_protected(
            query_context=query_context,
            memory_store=memory_store,
            session_id=session_id,
        )
        
        protected = _retokenize_memories(
            protected,
            token_counter,
        )
    
    else:
        protected = []
    
    # =========================================================
    # 8. Mandatory selection
    #
    # Mandatory = Protected + Required State
    #
    # 이 정보들은 Flexible Memory와 relevance 경쟁을 하지 않는다.
    # =========================================================

    mandatory_result = select_mandatory(
        protected=protected,
        states=states,
        memory_budget=memory_budget,
    )
    
    # =========================================================
    # 9. Mandatory budget failure
    #
    # C_mandatory > B_memory
    #
    # Mandatory를 버려서 행동을 계속하지 않고 Pipeline을 중단한다.
    # =========================================================

    if mandatory_result.status != PipelineStatus.OK:
        mandatory_ids = tuple(
            memory.memory_id
            for memory in mandatory_result.memories
        )
        
        mandatory_id_set = set(mandatory_ids)
        
        rejected_ids = _unique_ids(
            candidate.memory.memory_id
            for candidate in resolved
            if candidate.memory.memory_id not in mandatory_id_set
        )
        
        return SelectionResult(
            status=PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
            protected=tuple(protected),
            states=tuple(states),
            flexible=(),
            retrieved_memory_ids=retrieved_ids,
            selected_memory_ids=mandatory_ids,
            rejected_memory_ids=rejected_ids,
            memory_tokens=mandatory_result.used_tokens,
            remaining_budget=0,
        )
        
    # =========================================================
    # 10. Remove mandatory memories from flexible pool
    # =========================================================
    
    mandatory_ids = tuple(
        memory.memory_id
        for memory in mandatory_result.memories
    )
    
    flexible_candidates: list[MemoryCandidate] = []

    for candidate in resolved:
        memory = candidate.memory
        
        if memory.memory_id in mandatory_ids:
            continue
        
        if ablation.constraint_preservation and memory.memory_type == MemoryType.CONSTRAINT and memory.is_active and memory.is_protected:
            continue
        
        # =====================================================
        # 11. Flexible utility
        # =====================================================
        
        candidate.utility_score = compute_utility(
            candidate,
            query_context,
            weights=utility_weights,
        )
        
        flexible_candidates.append(candidate)
        
    # =========================================================
    # 12. Flexible budget
    #
    # B_flexible = B_memory - C_mandatory
    # =========================================================

    flexible_budget = mandatory_result.remaining_budget

    selected_flexible = select_flexible(
        candidates=flexible_candidates,
        budget=flexible_budget,
        token_aware=ablation.token_aware_packing,
    )

    flexible_memories = tuple(
        candidate.memory
        for candidate in selected_flexible
    )
    
    # =========================================================
    # 13. Final selection
    #
    # Protected
    #   ↓
    # Required State
    #   ↓
    # Flexible
    # =========================================================

    selected_memories = tuple(mandatory_result.memories) + flexible_memories
    
    selected_memories = _deduplicate_memories(selected_memories)
    
    selected_ids = tuple(memory.memory_id for memory in selected_memories)

    selected_id_set = set(selected_ids)
    
    # =========================================================
    # 14. Rejected memories
    # =========================================================
    
    rejected_ids = _unique_ids(
        candidate.memory.memory_id
        for candidate in resolved
        if candidate.memory.memory_id not in selected_id_set
    )
    
    # =========================================================
    # 15. Final accounting
    # =========================================================

    memory_tokens = sum(
        memory.token_count
        for memory in selected_memories
    )
    
    if memory_tokens > memory_budget:
        raise RuntimeError("selector produced memories exceeding the available memory budget")
    
    remaining_budget = memory_budget - memory_tokens

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


def _retokenize_memories(
    memories: Sequence[MemoryItem],
    token_counter: TokenCounter | None,
) -> list[MemoryItem]:
    return [
        _with_token_count(
            memory,
            token_counter,
        )
        for memory in memories
    ]
    
    
def _unique_ids(
    ids,
) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    
    for memory_id in ids:
        if memory_id in seen:
            continue
        
        seen.add(memory_id)
        result.append(memory_id)
    
    return tuple(result)


def _deduplicate_memories(
    memories: Sequence[MemoryItem],
) -> tuple[MemoryItem, ...]:
    result: list[MemoryItem] = []
    seen: set[str] = set()
    
    for memory in memories:
        if memory.memory_id in seen:
            continue
        
        seen.add(memory.memory_id)
        result.append(memory)
    
    return tuple(result)
