from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from agent.agent import Agent, ActResult, AgentRunResult
from agent.interaction import Interaction
from agent.tools.types import ToolCall, ToolResult

from context.builder import ContextBuildResult, build_context, fixed_context_tokens
from context.tokenizer import TokenCounter, provisional_token_count

from memory.baselines import Embedder, sliding_window, utility_per_token, vector_top_k
from memory.schema import MemoryItem
from memory.selector import PipelineStatus, SelectionResult


GoalChecker = Callable[
    [
        str | None,
        tuple[ToolCall, ...],
        tuple[ToolCall, ...],
    ],
    bool,
]


class SelectionPolicy(Protocol):
    def __call__(
        self, 
        *, 
        query: str,
        memories: Sequence[MemoryItem],
        session_id: str,
        total_budget: int,
        fixed_tokens: int,
        current_turn: int,
        token_counter: TokenCounter,
    ) -> SelectionResult:
        ...
        
        
@dataclass(frozen=True)
class StepTrace:
    step: int
    current_turn: int
    fixed_tokens: int
    
    selection_result: SelectionResult
    context_result: ContextBuildResult
    
    action_result: ActResult | None = None
    
    
@dataclass(frozen=True)
class PipelineRunResult:
    status: PipelineStatus
    stopped_reason: str
    goal_completed: bool
    
    response: str | None
    
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    interactions: tuple[Interaction, ...]
    
    steps: tuple[StepTrace, ...]
    
    total_input_tokens: int
    final_turn: int
    
    @property
    def agent_executed(self) -> bool:
        return any(
            step.action_result is not None
            for step in self.steps
        )
        
    @property
    def final_context_result(self) -> ContextBuildResult | None:
        if not self.steps:
            return None
        return self.steps[-1].context_result
    
    @property
    def final_selection_result(self) -> SelectionResult | None:
        if not self.steps:
            return None
        return self.steps[-1].selection_result
    
    def to_agent_run_result(self) -> AgentRunResult:
        return AgentRunResult(
            response=self.response,
            tool_calls=self.tool_calls,
            tool_results=self.tool_results,
            goal_completed=self.goal_completed,
            stopped_reason=self.stopped_reason,
            interactions=self.interactions,
        )
        
        
def make_baseline_policy(
    strategy: str,
    *,
    embedder: Embedder | None = None,
    top_k: int = 20,
    utility_weights: tuple[float, float, float] = (0.6, 0.3, 0.1),
) -> SelectionPolicy:
    strategies = (
        "sliding_window",
        "vector_top_k",
        "utility_per_token",
    )
    
    if strategy not in strategies:
        raise ValueError(f"unknown baseline strategy: {strategy}")
    
    if strategy != "sliding_window" and embedder is None:
        raise ValueError(f"{strategy} requires an embedder")
    
    def select(
        *,
        query: str,
        memories: Sequence[MemoryItem],
        session_id: str,
        total_budget: int,
        fixed_tokens: int,
        current_turn: int,
        token_counter: TokenCounter,
    ) -> SelectionResult:
        common = {
            "session_id": session_id,
            "total_budget": total_budget,
            "fixed_tokens": fixed_tokens,
            "current_turn": current_turn,
            "token_counter": token_counter,
        }
        
        if strategy == "sliding_window":
            return sliding_window(memories, **common)
        
        if strategy == "vector_top_k":
            return vector_top_k(
                query,
                memories,
                embedder=embedder,
                top_k=top_k,
                **common,
            )
            
        return utility_per_token(
            query,
            memories,
            embedder=embedder,
            top_k=top_k,
            weights=utility_weights,
            **common,
        )
        
    return select


def _tool_definitions_with_feedback(
    tool_definitions: str,
    interactions: Sequence[Interaction],
) -> str:
    if not interactions:
        return tool_definitions
    
    feedback = "\n".join(
        f"- {interaction.content}"
        for interaction in interactions
    )
    
    return tool_definitions.rstrip() + "\n\n[RECENT TOOL RESULTS]\n" + feedback


class Orchestrator:
    def __init__(
        self,
        *,
        agent: Agent,
        policy: SelectionPolicy,
        system_prompt: str,
        tool_definitions: str,
        token_counter: TokenCounter = provisional_token_count,
        max_steps: int = 8,
        goal_checker: GoalChecker | None = None,
    ) -> None:
        if not isinstance(agent, Agent):
            raise TypeError("agent must be an Agent instance")
        
        if not callable(policy):
            raise TypeError("policy must be callable")
        
        if not callable(token_counter):
            raise TypeError("token_counter must be callable")
        
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        
        if not isinstance(system_prompt, str):
            raise TypeError("system_prompt must be a string")
        
        if not isinstance(tool_definitions, str):
            raise TypeError("tool_definitions must be a string")
        
        if goal_checker is not None and not callable(goal_checker):
            raise TypeError("goal_checker must be callable")
        
        self.agent = agent
        self.policy = policy
        
        self.system_prompt = system_prompt
        self.tool_definitions = tool_definitions
        
        self.token_counter = token_counter
        self.max_steps = max_steps
        
        self.goal_checker = (
            goal_checker
            if goal_checker is not None else agent.goal_checker
        )
        
    def run(
        self,
        *,
        query: str,
        session_id: str,
        memories: Sequence[MemoryItem],
        total_budget: int,
        current_turn: int,
    ) -> PipelineRunResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        
        if type(total_budget) is not int or total_budget < 0:
            raise ValueError("total_budget must be a non-negative integer")
        
        if type(current_turn) is not int or current_turn < 0:
            raise ValueError("current_turn must be a non-negative integer")
        
        if any(
            not isinstance(memory, MemoryItem)
            for memory in memories
        ):
            raise TypeError("memories must contain MemoryItem objects")
        
        memory_snapshot = tuple(memories)
        
        tool_calls: list[ToolCall] = []
        tool_results: list[ToolResult] = []
        interactions: list[Interaction] = []
        
        traces: list[StepTrace] = []
        
        total_input_tokens = 0
        response: str | None = None
        
        self.agent.current_turn = current_turn
        
        def finish(
            *,
            status: PipelineStatus,
            reason: str,
            completed: bool = False,
        ) -> PipelineRunResult:
            return PipelineRunResult(
                status=status,
                stopped_reason=reason,
                goal_completed=completed,
                response=response,
                tool_calls=tuple(tool_calls),
                tool_results=tuple(tool_results),
                interactions=tuple(interactions),
                steps=tuple(traces),
                total_input_tokens=total_input_tokens,
                final_turn=current_turn,
            )
            
        for step_number in range(1, self.max_steps + 1):
            effective_tool_definitions = _tool_definitions_with_feedback(
                self.tool_definitions,
                interactions
            )
            
            fixed_tokens = fixed_context_tokens(
                system_prompt=self.system_prompt,
                tool_definitions=effective_tool_definitions,
                query=query,
                token_counter=self.token_counter,
            )
            
            if fixed_tokens > total_budget:
                selection = SelectionResult(status=PipelineStatus.INVALID_BUDGET_CONFIGURATION)
            else:
                selection = self.policy(
                    query=query,
                    memories=memory_snapshot,
                    session_id=session_id,
                    total_budget=total_budget,
                    fixed_tokens=fixed_tokens,
                    current_turn=current_turn,
                    token_counter=self.token_counter,
                )
                
                if not isinstance(selection, SelectionResult):
                    raise TypeError("policy must return SelectionResult")
                
            context_result = build_context(
                system_prompt=self.system_prompt,
                tool_definitions=effective_tool_definitions,
                query=query,
                selection=selection,
                total_budget=total_budget,
                token_counter=self.token_counter,
            )
            
            if context_result.status != PipelineStatus.OK:
                traces.append(
                    StepTrace(
                        step=step_number,
                        current_turn=current_turn,
                        fixed_tokens=fixed_tokens,
                        selection_result=selection,
                        context_result=context_result,
                    )
                )
                
                reason = (
                    "context_budget"
                    if context_result.status == PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET else context_result.status.value
                )
                
                return finish(
                    status=context_result.status,
                    reason=reason,
                )
                
            if context_result.context is None:
                raise RuntimeError("OK context result must contain context")
            
            total_input_tokens += context_result.total_input_tokens
            
            acted = self.agent.act(
                context_result.context,
                session_id=session_id,
            )
            
            traces.append(
                StepTrace(
                    step=step_number,
                    current_turn=current_turn,
                    fixed_tokens=fixed_tokens,
                    selection_result=selection,
                    context_result=context_result,
                    action_result=acted,
                )
            )
            
            tool_calls.extend(acted.tool_calls)
            tool_results.extend(acted.tool_results)
            interactions.extend(acted.interactions)
            
            response = acted.response
            
            if acted.interactions:
                for interaction in acted.interactions:
                    if interaction.session_id != session_id:
                        raise ValueError("interaction session_id mismatch")
                    
                    if interaction.turn_id <= current_turn:
                        raise ValueError("interaction turn_id must advance")
                    
                current_turn = max(
                    interaction.turn_id
                    for interaction in interactions
                )
                
            if acted.stopped_reason == "completed":
                completed = False
                
                if self.goal_checker is not None:
                    completed = bool(
                        self.goal_checker(
                            response,
                            tuple(tool_calls),
                            tuple(tool_results),
                        )
                    )
                    
                return finish(
                    status=PipelineStatus.OK,
                    reason="completed",
                    completed=completed,
                )
                
            if acted.stopped_reason == "tool_failure":
                return finish(
                    status=PipelineStatus.OK,
                    reason="tool_failure",
                )
                
            if acted.stopped_reason != "tool_called":
                raise RuntimeError(f"unexpected Agent stopped_reason: {acted.stopped_reason}")
            
        return finish(
            status=PipelineStatus.OK,
            reason="max_steps",
        )