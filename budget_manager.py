"""Budget-aware context construction: C_P is mandatory, B_flex is utility-ranked."""

from __future__ import annotations

import re
from typing import List, Tuple

from memory_schema import MemoryRecord, MemoryType, ValidStatus, count_tokens


class BudgetAwareContextBuilder:
    """Select prompt contents under a hard token budget ``B``.

    Mandatory cost:
        C_P = tokens(PROTECTED, ACTIVE) + tokens(CURRENT_STATE, ACTIVE)

    Flexible remainder:
        B_flex = B - C_P

    Flexible memories are ranked by query utility (lexical overlap + recency)
    and packed greedily until ``B_flex`` is exhausted. Packing charges the
    rendered prompt line (not just raw content) so ``used_tokens`` stays
    inside ``B`` whenever C_P itself fits.
    """

    # Section headers counted against the same tokenizer so used_tokens is honest.
    _HEADER_PROTECTED = "[PROTECTED MEMORY]"
    _HEADER_STATE = "[CURRENT STATE]"
    _HEADER_FLEXIBLE = "[FLEXIBLE MEMORY]"
    _HEADER_QUERY = "[QUERY]"

    def __init__(self, total_budget: int) -> None:
        if total_budget <= 0:
            raise ValueError("total_budget must be a positive token count.")
        self.total_budget = total_budget

    def build_context(
        self,
        all_memories: List[MemoryRecord],
        query: str,
    ) -> Tuple[str, dict]:
        """Assemble a prompt and return ``(context_string, statistics)``."""

        mandatory = [m for m in all_memories if m.is_mandatory()]
        flexible_pool = [
            m
            for m in all_memories
            if m.memory_type == MemoryType.FLEXIBLE
            and m.valid_status == ValidStatus.ACTIVE
        ]

        # Paper formula: C_P is the token cost of mandatory memory contents.
        c_p_tokens = sum(m.token_count for m in mandatory)
        b_flex_tokens = max(self.total_budget - c_p_tokens, 0)

        selected_flexible = self._select_flexible(
            flexible_pool, query, b_flex_tokens, mandatory
        )

        context = self._format_context(mandatory, selected_flexible, query)
        used_tokens = count_tokens(context)

        stats = {
            "c_p_tokens": c_p_tokens,
            "b_flex_tokens": b_flex_tokens,
            "used_tokens": used_tokens,
            "selected_count": len(mandatory) + len(selected_flexible),
            "mandatory_count": len(mandatory),
            "flexible_selected_count": len(selected_flexible),
            "budget_overflow": used_tokens > self.total_budget,
            "total_budget": self.total_budget,
        }
        return context, stats

    def _select_flexible(
        self,
        candidates: List[MemoryRecord],
        query: str,
        budget: int,
        mandatory: List[MemoryRecord],
    ) -> List[MemoryRecord]:
        """Greedy knapsack: pack highest utility-per-token items into B_flex."""

        if budget <= 0 or not candidates:
            return []

        scored: List[Tuple[float, MemoryRecord]] = [
            (self._utility(memory, query), memory) for memory in candidates
        ]
        # Density ranking prefers informative, compact memories under a tight budget.
        scored.sort(
            key=lambda item: (
                item[0] / max(item[1].token_count, 1),
                item[1].source_turn,
            ),
            reverse=True,
        )

        selected: List[MemoryRecord] = []
        remaining = budget
        for _utility_score, memory in scored:
            line_cost = count_tokens(self._memory_line(memory))
            if line_cost > remaining:
                continue
            tentative = selected + [memory]
            tentative.sort(key=lambda m: (m.source_turn, m.id))
            # Reject items whose formatting overhead would blow the total budget.
            rendered = self._format_context(mandatory, tentative, query)
            if count_tokens(rendered) > self.total_budget:
                continue
            selected.append(memory)
            remaining -= line_cost

        # Keep prompt order chronological so the agent sees a coherent timeline.
        selected.sort(key=lambda m: (m.source_turn, m.id))
        return selected

    def _utility(self, memory: MemoryRecord, query: str) -> float:
        """Cheap lexical utility: overlap with the query, boosted by recency."""

        query_tokens = set(self._tokenize(query))
        content_tokens = set(self._tokenize(memory.content))
        if not query_tokens:
            overlap = 0.0
        else:
            overlap = len(query_tokens & content_tokens) / len(query_tokens)

        # Recency in [0, 1], assuming a 120-turn horizon (clamped for safety).
        recency = min(max(memory.source_turn, 0), 120) / 120.0
        return 0.75 * overlap + 0.25 * recency

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"[a-zA-Z0-9가-힣]+", text.lower())

    def _format_context(
        self,
        mandatory: List[MemoryRecord],
        flexible: List[MemoryRecord],
        query: str,
    ) -> str:
        protected = [m for m in mandatory if m.memory_type == MemoryType.PROTECTED]
        current_state = [
            m for m in mandatory if m.memory_type == MemoryType.CURRENT_STATE
        ]

        sections: List[str] = [
            self._render_section(self._HEADER_PROTECTED, protected),
            self._render_section(self._HEADER_STATE, current_state),
            self._render_section(self._HEADER_FLEXIBLE, flexible),
            f"{self._HEADER_QUERY}\n{query}",
        ]
        return "\n\n".join(sections)

    def _render_section(self, header: str, memories: List[MemoryRecord]) -> str:
        if not memories:
            return f"{header}\n(none)"
        lines = [header]
        for memory in memories:
            lines.append(self._memory_line(memory))
        return "\n".join(lines)

    @staticmethod
    def _memory_line(memory: MemoryRecord) -> str:
        return (
            f"- [{memory.scope} | v{memory.version} | turn {memory.source_turn}] "
            f"{memory.content}"
        )
