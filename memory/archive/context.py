"""interfaces.md 17절에 따른 프롬프트 조립과 최종 토큰 예산 검증."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from memory.schema import MemoryItem
from memory.selector import PipelineStatus, SelectionResult


@dataclass(frozen=True)
class ContextBuildResult:
    status: PipelineStatus
    context: str | None
    selected_memory_ids: tuple[str, ...]
    dropped_flexible_ids: tuple[str, ...]
    protected_tokens: int
    state_tokens: int
    flexible_tokens: int
    total_input_tokens: int
    unused_budget: int


def provisional_token_count(text: str) -> int:
    """공용 토크나이저 도입 전 임시 단어 수. 실험용 토큰 측정값은 아니다."""
    return len(text.split())


def _memory_section(heading: str, memories: Sequence[MemoryItem]) -> str:
    if not memories:
        return ""
    return "\n".join([heading, *[f"- {item.content}" for item in memories]])


def _format_context(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    memory_sections: Sequence[str],
) -> str:
    sections = [
        f"[SYSTEM]\n{system_prompt.strip()}",
        f"[TOOL DEFINITIONS]\n{tool_definitions.strip()}",
        *[section for section in memory_sections if section],
        f"[CURRENT TASK]\n{query.strip()}",
    ]
    return "\n\n".join(sections)


def fixed_context_tokens(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    token_counter: Callable[[str], int] | None = None,
) -> int:
    """기억을 제외하고 섹션 제목을 포함한 고정 프롬프트 비용을 계산한다."""
    counter = token_counter if token_counter is not None else provisional_token_count
    return counter(_format_context(system_prompt, tool_definitions, query, ()))


def build_context(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    selection: SelectionResult,
    total_budget: int,
    token_counter: Callable[[str], int] | None = None,
) -> ContextBuildResult:
    """선택된 기억을 포맷하고 예산 초과 시 Flexible만 뒤에서부터 제거한다.

    섹션 비용과 최종 비용은 같은 counter로 측정한다. 포맷 경계 때문에
    개별 섹션 비용의 합이 전체 입력 비용과 같다고 가정하지 않는다.
    실패 시 실행 가능한 context를 반환하지 않는다.
    """
    if type(total_budget) is not int or total_budget < 0:
        raise ValueError("total_budget must be a non-negative integer")
    counter = token_counter if token_counter is not None else provisional_token_count
    if selection.status != PipelineStatus.OK:
        return ContextBuildResult(
            status=selection.status, context=None,
            selected_memory_ids=selection.selected_memory_ids,
            dropped_flexible_ids=(), protected_tokens=0, state_tokens=0,
            flexible_tokens=0, total_input_tokens=0, unused_budget=0,
        )

    fixed_tokens = fixed_context_tokens(system_prompt, tool_definitions, query, counter)
    if fixed_tokens > total_budget:
        return ContextBuildResult(
            status=PipelineStatus.INVALID_BUDGET_CONFIGURATION, context=None,
            selected_memory_ids=(), dropped_flexible_ids=(),
            protected_tokens=0, state_tokens=0, flexible_tokens=0,
            total_input_tokens=fixed_tokens, unused_budget=0,
        )

    flexible = list(selection.flexible)
    dropped: list[str] = []
    protected_section = _memory_section("[PROTECTED CONSTRAINTS]", selection.protected)
    state_section = _memory_section("[STATE]", selection.states)
    while True:
        flexible_section = _memory_section("[RELEVANT MEMORY]", flexible)
        context = _format_context(
            system_prompt, tool_definitions, query,
            (protected_section, state_section, flexible_section),
        )
        total = counter(context)
        if total <= total_budget or not flexible:
            fits = total <= total_budget
            selected = (*selection.protected, *selection.states, *flexible)
            return ContextBuildResult(
                status=(PipelineStatus.OK if fits
                        else PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET),
                context=context if fits else None,
                selected_memory_ids=tuple(item.memory_id for item in selected),
                dropped_flexible_ids=tuple(dropped),
                protected_tokens=counter(protected_section) if protected_section else 0,
                state_tokens=counter(state_section) if state_section else 0,
                flexible_tokens=counter(flexible_section) if flexible_section else 0,
                total_input_tokens=total,
                unused_budget=max(0, total_budget - total),
            )
        dropped.append(flexible.pop().memory_id)
