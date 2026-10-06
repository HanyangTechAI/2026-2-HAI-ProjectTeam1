"""SelectionResult를 LLM 프롬프트로 조립한다.

토큰 수는 호출자가 넘긴 token_counter 하나로 계산한다. 고정 프롬프트,
섹션, 최종 입력이 서로 다른 단위를 쓰지 않는다. 공용 토크나이저가
정해지기 전에는 provisional_token_count를 쓴다. 상태 섹션 제목은
[STATE]이다. 과거 버전을 현재 상태라고 부르지 않기 위해서다.
포맷 후 예산이 넘으면 우선순위가 낮은 Flexible Memory부터 제거하고
다시 센다. Protected Constraint와 Required State는 제거하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from memory.schema import MemoryItem
from memory.selector import PipelineStatus, SelectionResult


@dataclass(frozen=True)
class ContextBuildResult:
    """Context Builder의 출력. docs/interfaces.md 17절."""

    status: PipelineStatus
    context: str | None
    selected_memory_ids: tuple[str, ...]
    dropped_flexible_ids: tuple[str, ...]
    protected_tokens: int
    state_tokens: int
    flexible_tokens: int
    total_input_tokens: int
    unused_budget: int


STATE_HEADING = "[STATE]"


def provisional_token_count(text: str) -> int:
    """공백으로 나눈 단어 수. 공용 토크나이저의 임시 대체다.

    실험 지표로 쓰려면 Agent에 같은 실제 토크나이저를 넣어야 한다.
    이 함수를 쓸 때도 고정 비용, 섹션 비용, 최종 입력은 같은 함수로 센다.
    """
    return len(text.split())


def fixed_context_tokens(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    token_counter: Callable[[str], int] | None = None,
) -> int:
    """기억이 없는 프롬프트의 토큰 수. 섹션 제목을 포함한다."""
    counter = token_counter or provisional_token_count
    return counter(_format(system_prompt, tool_definitions, query, (), (), []))


def build_context(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    selection: SelectionResult,
    total_budget: int,
    token_counter: Callable[[str], int] | None = None,
) -> ContextBuildResult:
    """선택 결과를 섹션 프롬프트로 만들고 최종 토큰 수를 맞춘다."""
    counter = token_counter or provisional_token_count
    if selection.status != PipelineStatus.OK:
        return _empty(selection.status, selection)

    flexible = list(selection.flexible)
    dropped: list[str] = []
    while True:
        context = _format(system_prompt, tool_definitions, query, selection.protected, selection.states, flexible)
        total = counter(context)
        if total <= total_budget:
            selected = tuple(selection.protected) + tuple(selection.states) + tuple(flexible)
            return ContextBuildResult(
                status=PipelineStatus.OK,
                context=context,
                selected_memory_ids=tuple(item.memory_id for item in selected),
                dropped_flexible_ids=tuple(dropped),
                protected_tokens=_section_tokens(counter, "[PROTECTED CONSTRAINTS]", selection.protected),
                state_tokens=_section_tokens(counter, STATE_HEADING, selection.states),
                flexible_tokens=_section_tokens(counter, "[RELEVANT MEMORY]", flexible),
                total_input_tokens=total,
                unused_budget=total_budget - total,
            )
        if not flexible:
            return ContextBuildResult(
                status=PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
                context=None,
                selected_memory_ids=tuple(item.memory_id for item in selection.protected + selection.states),
                dropped_flexible_ids=tuple(dropped),
                protected_tokens=_section_tokens(counter, "[PROTECTED CONSTRAINTS]", selection.protected),
                state_tokens=_section_tokens(counter, STATE_HEADING, selection.states),
                flexible_tokens=0,
                total_input_tokens=total,
                unused_budget=0,
            )
        dropped.append(flexible.pop().memory_id)


def _format(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    protected: tuple[MemoryItem, ...] | list[MemoryItem],
    states: tuple[MemoryItem, ...] | list[MemoryItem],
    flexible: list[MemoryItem],
) -> str:
    sections = [
        "[SYSTEM]",
        system_prompt.strip(),
        "",
        "[TOOL DEFINITIONS]",
        tool_definitions.strip(),
        "",
    ]
    if protected:
        sections.extend(["[PROTECTED CONSTRAINTS]", *[f"- {item.content}" for item in protected], ""])
    if states:
        sections.extend([STATE_HEADING, *[f"- {item.content}" for item in states], ""])
    if flexible:
        sections.extend(["[RELEVANT MEMORY]", *[f"- {item.content}" for item in flexible], ""])
    sections.extend(["[CURRENT TASK]", query.strip()])
    return "\n".join(sections)


def _section_tokens(
    counter: Callable[[str], int],
    heading: str,
    items: tuple[MemoryItem, ...] | list[MemoryItem],
) -> int:
    if not items:
        return 0
    return counter("\n".join([heading, *[f"- {item.content}" for item in items]]))


def _empty(status: PipelineStatus, selection: SelectionResult) -> ContextBuildResult:
    return ContextBuildResult(
        status=status,
        context=None,
        selected_memory_ids=selection.selected_memory_ids,
        dropped_flexible_ids=(),
        protected_tokens=0,
        state_tokens=0,
        flexible_tokens=0,
        total_input_tokens=0,
        unused_budget=0,
    )
