from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from memory.schema import MemoryItem
from memory.selector import PipelineStatus, SelectionResult

from .tokenizer import provisional_token_count, TokenCounter, count_tokens


@dataclass(frozen=True)
class ContextBuildResult:
    status: PipelineStatus
    context: str | None
    
    selected_memory_ids: tuple[str, ...] = ()
    dropped_flexible_ids: tuple[str, ...] = ()
    
    protected_tokens: int = 0
    state_tokens: int = 0
    flexible_tokens: int = 0
    
    total_input_tokens: int = 0
    unused_budget: int = 0


def fixed_context_tokens(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    token_counter: TokenCounter | None = None,
) -> int:
    counter = token_counter or provisional_token_count
    
    context = _assemble_context(
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        query=query,
        protected=(),
        states=(),
        flexible=(),
    )
    
    return count_tokens(context, counter)


def build_context(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    selection: SelectionResult,
    total_budget: int,
    token_counter: TokenCounter | None = None,
) -> ContextBuildResult:
    if total_budget < 0:
        raise ValueError("total_budget must be non-negative")
    
    counter = token_counter or provisional_token_count
    
    if selection.status != PipelineStatus.OK:
        return ContextBuildResult(
            status=selection.status,
            context=None,
            unused_budget=total_budget,
        )
        
    protected = list(selection.protected)
    states = list(selection.states)
    flexible = list(selection.flexible)
    
    dropped_flexible_ids: list[str] = []
    
    while True:
        context = _assemble_context(
            system_prompt=system_prompt,
            tool_definitions=tool_definitions,
            query=query,
            protected=protected,
            states=states,
            flexible=flexible,
        )
        
        total_tokens = count_tokens(context, counter)
        
        if total_tokens <= total_budget:
            protected_tokens = _section_token_count(
                "PROTECTED CONSTRAINTS", 
                protected, 
                counter,
            )
            state_tokens = _section_token_count(
                "STATE", 
                states, 
                counter,
            )
            flexible_tokens = _section_token_count(
                "RELEVANT MEMORY", 
                flexible,
                counter,
            )
            
            selected_memory_ids = tuple(
                memory.memory_id
                for memory in protected + states + flexible
            )
            
            return ContextBuildResult(
                status=PipelineStatus.OK,
                context=context,
                selected_memory_ids=selected_memory_ids,
                dropped_flexible_ids=tuple(dropped_flexible_ids),
                protected_tokens=protected_tokens,
                state_tokens=state_tokens,
                flexible_tokens=flexible_tokens,
                total_input_tokens=total_tokens,
                unused_budget=total_budget - total_tokens,
            )
        
        if flexible:
            removed = flexible.pop()
            dropped_flexible_ids.append(removed.memory_id)
            continue
        
        protected_tokens = _section_token_count(
            "PROTECTED CONSTRAINTS", 
            protected, 
            counter,
        )
        state_tokens = _section_token_count(
            "STATE", 
            states, 
            counter,
        )
        
        mandatory_ids = tuple(
            memory.memory_id
            for memory in protected + states
        )
        
        return ContextBuildResult(
            status=PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
            context=None,
            selected_memory_ids=mandatory_ids,
            dropped_flexible_ids=tuple(dropped_flexible_ids),
            protected_tokens=protected_tokens,
            state_tokens=state_tokens,
            flexible_tokens=0,
            total_input_tokens=total_tokens,
            unused_budget=0,
        )
    
def _assemble_context(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    protected: Sequence[MemoryItem],
    states: Sequence[MemoryItem],
    flexible: Sequence[MemoryItem],
) -> str:
    sections: list[str] = []
    
    sections.append(
        _text_section(
            "SYSTEM",
            system_prompt,
        )
    )
    
    sections.append(
        _text_section(
            "TOOL DEFINITIONS",
            tool_definitions,
        )
    )
    
    protected_selection = _memory_section(
        "PROTECTED CONSTRAINTS", 
        protected,
    )
    if protected_selection is not None:
        sections.append(protected_selection)
    
    states_section = _memory_section(
        "STATE", 
        states,
    )
    if states_section is not None:
        sections.append(states_section)
    
    flexible_selection = _memory_section(
        "RELEVANT MEMORY", 
        flexible,
    )
    if flexible_selection is not None:
        sections.append(flexible_selection)
        
    sections.append(
        _text_section(
            "CURRENT TASK",
            query,
        )
    )
    
    
    return "\n\n".join(sections)


def _text_section(
    title: str,
    content: str,
) -> str:
    cleaned = content.strip()
    
    if cleaned:
        return f"[{title}]\n{cleaned}"
    
    return f"[{title}]"


def _memory_section(
    title: str,
    memories: Sequence[MemoryItem],
) -> str | None:
    if not memories:
        return None
    
    lines = [
        f"- {memory.content.strip()}"
        for memory in memories
    ]
    
    return f"[{title}]\n" + "\n".join(lines)


def _section_token_count(
    title: str,
    memories: Sequence[MemoryItem],
    token_counter: TokenCounter,
) -> int:
    section = _memory_section(
        title,
        memories,
    )
    
    if section is None:
        return 0
    
    return count_tokens(section, token_counter)
