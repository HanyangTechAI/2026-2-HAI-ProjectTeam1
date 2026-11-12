
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from context.tokenizer import TokenCounter
from memory import selector as frozen
from memory.schema import MemoryItem, MemoryType
from memory.store import SnapshotMemoryStore
from memory.task_criticality import CriticalPackingResult, pack_task_criticality
from memory.retrieval import Embedder


def make_criticality_policy(
    *,
    embedder: Embedder,
    top_k: int = 5,
    utility_weights: tuple[float, float, float] = frozen.DEFAULT_UTILITY_WEIGHTS,
    fallback_alpha: float = 1.0,
    on_decision: Callable[[CriticalPackingResult], None] | None = None,
) -> Callable:
    """Return an Orchestrator-compatible experimental SelectionPolicy."""
    from memory.experimental_packing import validate_alpha

    if not callable(embedder):
        raise TypeError("embedder must be callable")
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    validate_alpha(fallback_alpha)
    if on_decision is not None and not callable(on_decision):
        raise TypeError("on_decision must be callable or None")

    def select(
        *,
        query: str,
        memories: Sequence[MemoryItem],
        session_id: str,
        total_budget: int,
        fixed_tokens: int,
        current_turn: int,
        token_counter: TokenCounter,
        current_time: datetime | None = None,
    ) -> frozen.SelectionResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a nonempty string")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a nonempty string")
        if type(current_turn) is not int or current_turn < 0:
            raise ValueError("current_turn must be a nonnegative integer")
        if not isinstance(current_time, datetime):
            raise TypeError("current_time must be datetime")
        if token_counter is not None and not callable(token_counter):
            raise TypeError("token_counter must be callable")
        if any(not isinstance(m, MemoryItem) for m in memories):
            raise TypeError("memories must contain MemoryItem objects")

        status, memory_budget = frozen.calculate_memory_budget(
            total_budget, fixed_tokens
        )

        if status != frozen.PipelineStatus.OK:
            return frozen.SelectionResult(
                status=status,
                remaining_budget=0,
            )

        store = SnapshotMemoryStore(tuple(
            m for m in memories
            if m.session_id == session_id and m.turn_id <= current_turn
        ))

        query_context = frozen.analyze_query(
            query,
            current_turn,
            current_time,
        )

        retrieved = frozen.retrieve_candidates(
            query_context=query_context,
            memory_store=store,
            session_id=session_id,
            embedder=embedder,
            top_k=top_k,
            exclude_active_protected=True,
        )

        retrieved_ids = tuple(
            c.memory.memory_id for c in retrieved
        )

        resolved = frozen.resolve_temporal_versions(
            frozen.expand_version_chains(retrieved, store),
            query_context,
        )

        resolved = frozen._retokenize_candidates(
            resolved,
            token_counter,
        )

        states = frozen._retokenize_memories(
            frozen.resolve_required_states(
                resolved,
                query_context,
            ),
            token_counter,
        )

        protected = frozen._retokenize_memories(
            frozen.retrieve_protected(
                query_context,
                store,
                session_id,
            ),
            token_counter,
        )

        mandatory = frozen.select_mandatory(
            protected,
            states,
            memory_budget,
        )

        mandatory_ids = tuple(
            m.memory_id for m in mandatory.memories
        )

        mandatory_set = set(mandatory_ids)

        if mandatory.status != frozen.PipelineStatus.OK:
            rejected = frozen._unique_ids(
                c.memory.memory_id
                for c in resolved
                if c.memory.memory_id not in mandatory_set
            )

            return frozen.SelectionResult(
                status=frozen.PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
                protected=tuple(protected),
                states=tuple(states),
                retrieved_memory_ids=retrieved_ids,
                selected_memory_ids=mandatory_ids,
                rejected_memory_ids=rejected,
                memory_tokens=mandatory.used_tokens,
                remaining_budget=0,
            )

        flexible = []

        for c in resolved:
            m = c.memory

            if m.memory_id in mandatory_set:
                continue

            if (
                m.memory_type == MemoryType.CONSTRAINT
                and m.is_active
                and m.is_protected
            ):
                continue

            c.utility_score = frozen.compute_utility(
                c,
                query_context,
                weights=utility_weights,
            )

            flexible.append(c)

        decision = pack_task_criticality(
            flexible,
            query=query,
            mandatory=mandatory.memories,
            budget=mandatory.remaining_budget,
            fallback_alpha=fallback_alpha,
        )

        if on_decision is not None:
            on_decision(decision)

        selected_flexible = tuple(
            c.memory for c in decision.selected
        )

        selected_ids = mandatory_ids + tuple(
            m.memory_id for m in selected_flexible
        )

        selected_set = set(selected_ids)

        if len(selected_ids) != len(selected_set):
            raise AssertionError(
                "duplicate memory selection"
            )

        rejected_ids = frozen._unique_ids(
            c.memory.memory_id
            for c in resolved
            if c.memory.memory_id not in selected_set
        )

        tokens = mandatory.used_tokens + sum(
            m.token_count for m in selected_flexible
        )

        if tokens > memory_budget:
            raise AssertionError(
                "Criticality-Aware selector exceeded memory budget"
            )

        return frozen.SelectionResult(
            status=frozen.PipelineStatus.OK,
            protected=tuple(protected),
            states=tuple(states),
            flexible=selected_flexible,
            retrieved_memory_ids=retrieved_ids,
            selected_memory_ids=selected_ids,
            rejected_memory_ids=rejected_ids,
            memory_tokens=tokens,
            remaining_budget=memory_budget - tokens,
        )

    return select
