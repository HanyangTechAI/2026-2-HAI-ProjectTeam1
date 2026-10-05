"""Agent 실행 스켈레톤.

Memory 내부를 수정하지 않는다. 기억 선택은 selector.select_memory에 맡기고,
완성된 Context만 보고 plan한다. LLM 연결은 아직 없으므로 plan은 주입된
planner가 있을 때만 동작한다.

예산 상태가 INVALID_BUDGET_CONFIGURATION 또는
INSUFFICIENT_CONTEXT_BUDGET이면 LLM과 Tool Executor를 호출하지 않는다.
툴 결과를 붙인 다음 프롬프트가 예산을 넘으면 다음 plan도 호출하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from memory.selector import PipelineStatus, SelectionResult, select_memory
from memory.store import MemoryStore

from .context import (
    ContextBuildResult,
    build_context,
    fixed_context_tokens,
    provisional_token_count,
)
from .interaction import Interaction, interactions_from_tool_results
from .tools.executor import ToolExecutor
from .tools.types import ToolCall, ToolResult

KST = timezone(timedelta(hours=9))
DEFAULT_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=KST)

GoalChecker = Callable[[str | None, tuple[ToolCall, ...], tuple[ToolResult, ...]], bool]


@dataclass(frozen=True)
class AgentAction:
    """plan의 한 단계. 툴을 부르지 않으면 tool_name과 action은 None이다."""

    tool_name: str | None
    action: str | None
    arguments: dict
    text_response: str | None


@dataclass(frozen=True)
class TokenUsage:
    """한 실행에서 같은 token_counter로 센 입력과 출력.

    input_tokens는 plan에 실제로 넘긴 프롬프트 중 가장 큰 값이다.
    파이프라인이 모델 호출 전에 끝나면 None이다.
    """

    fixed_input_tokens: int
    input_tokens: int | None
    output_tokens: int


@dataclass(frozen=True)
class ActResult:
    """완성된 Context 이후의 계획과 툴 실행 결과."""

    response: str | None
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    stopped_reason: str
    input_tokens: int
    output_tokens: int
    interactions: tuple[Interaction, ...]


@dataclass(frozen=True)
class AgentRunResult:
    """run_task의 출력. 선택 결과와 행동 결과를 함께 남긴다."""

    status: PipelineStatus
    response: str | None
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    selection_result: SelectionResult | None
    context_result: ContextBuildResult | None
    goal_completed: bool
    usage: TokenUsage
    stopped_reason: str
    interactions: tuple[Interaction, ...]


class Agent:
    """Context를 받아 행동을 고르고 mock 툴을 실행하는 실행기.

    planner가 None이면 plan은 NotImplementedError를 낸다. 데모와 테스트는
    planner로 스크립트 또는 가짜 LLM을 넣는다.
    goal_checker가 없으면 goal_completed는 False다. 툴을 호출했다는 이유만으로
    작업 성공으로 기록하지 않는다.
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        *,
        system_prompt: str,
        tool_definitions: str,
        executor: ToolExecutor | None = None,
        planner: Callable[[str], AgentAction] | None = None,
        goal_checker: GoalChecker | None = None,
        token_counter: Callable[[str], int] | None = None,
        current_turn: int = 0,
        clock: Callable[[], datetime] | None = None,
        max_steps: int = 8,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        self.memory_store = memory_store
        self.system_prompt = system_prompt
        self.tool_definitions = tool_definitions
        self.executor = executor or ToolExecutor()
        self.planner = planner
        self.goal_checker = goal_checker
        self.token_counter = token_counter or provisional_token_count
        self.current_turn = current_turn
        self.clock = clock or (lambda: DEFAULT_NOW)
        self.max_steps = max_steps

    def plan(self, context: str) -> AgentAction:
        """완성된 Context에서 다음 행동 하나를 고른다.

        LLM planner는 이 메서드에 연결한다. 툴 결과는 호출자가 Context 뒤에
        붙여 다음 plan 호출에 넘긴다.
        """
        if self.planner is None:
            raise NotImplementedError(
                "LLM planner is not connected. Pass planner to Agent for tests and demos."
            )
        return self.planner(context)

    def run_task(self, task: str, session_id: str, context_budget: int) -> AgentRunResult:
        """기억 선택, Context 조립, 계획, 툴 실행을 한 번 수행한다."""
        if context_budget < 0:
            raise ValueError("context_budget must be non-negative")

        fixed_tokens = fixed_context_tokens(
            self.system_prompt,
            self.tool_definitions,
            task,
            self.token_counter,
        )
        selection = select_memory(
            query=task,
            session_id=session_id,
            memory_store=self.memory_store,
            total_budget=context_budget,
            fixed_tokens=fixed_tokens,
            current_turn=self.current_turn,
            current_time=self.clock(),
        )
        if selection.status != PipelineStatus.OK:
            return self._stopped_before_model(
                selection.status,
                selection,
                None,
                fixed_tokens,
                "invalid_budget" if selection.status == PipelineStatus.INVALID_BUDGET_CONFIGURATION
                else "insufficient_context",
            )

        context_result = build_context(
            self.system_prompt,
            self.tool_definitions,
            task,
            selection,
            context_budget,
            self.token_counter,
        )
        if context_result.status != PipelineStatus.OK or context_result.context is None:
            return self._stopped_before_model(
                context_result.status,
                selection,
                context_result,
                fixed_tokens,
                "insufficient_context",
            )

        acted = self.act(
            context_result.context,
            context_budget=context_budget,
            session_id=session_id,
        )
        return AgentRunResult(
            status=PipelineStatus.OK,
            response=acted.response,
            tool_calls=acted.tool_calls,
            tool_results=acted.tool_results,
            selection_result=selection,
            context_result=context_result,
            goal_completed=self._goal_completed(acted),
            usage=TokenUsage(fixed_tokens, acted.input_tokens, acted.output_tokens),
            stopped_reason=acted.stopped_reason,
            interactions=acted.interactions,
        )

    def act(self, context: str, *, context_budget: int, session_id: str) -> ActResult:
        """이미 만들어진 Context로 계획과 툴 실행만 반복한다.

        plan에 넘기는 프롬프트가 context_budget을 넘으면 그 호출은 하지 않는다.
        """
        if context_budget < 0:
            raise ValueError("context_budget must be non-negative")

        calls: list[ToolCall] = []
        results: list[ToolResult] = []
        transcript = context
        response: str | None = None
        input_tokens = 0
        output_tokens = 0
        stopped = "max_steps"
        timestamp = self.clock()

        for _ in range(self.max_steps):
            prompt_tokens = self.token_counter(transcript)
            if prompt_tokens > context_budget:
                stopped = "context_budget"
                break
            action = self.plan(transcript)
            input_tokens = max(input_tokens, prompt_tokens)
            if action.tool_name is None and action.action is None:
                response = action.text_response
                output_tokens = self.token_counter(response or "")
                stopped = "completed"
                break
            if action.tool_name is None or action.action is None:
                stopped = "tool_failure"
                break
            call = ToolCall(action.tool_name, action.action, dict(action.arguments))
            result = self.executor.execute(call)
            calls.append(call)
            results.append(result)
            transcript = _append_tool_result(transcript, result)
            if not result.success:
                response = action.text_response
                output_tokens = self.token_counter(response or "")
                stopped = "tool_failure"
                break

        acted = ActResult(
            response=response,
            tool_calls=tuple(calls),
            tool_results=tuple(results),
            stopped_reason=stopped,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            interactions=interactions_from_tool_results(
                session_id,
                self.current_turn,
                tuple(results),
                timestamp,
            ),
        )
        return acted

    def _goal_completed(self, acted: ActResult) -> bool:
        if acted.stopped_reason != "completed" or self.goal_checker is None:
            return False
        return bool(self.goal_checker(acted.response, acted.tool_calls, acted.tool_results))

    def _stopped_before_model(
        self,
        status: PipelineStatus,
        selection: SelectionResult,
        context_result: ContextBuildResult | None,
        fixed_tokens: int,
        stopped_reason: str,
    ) -> AgentRunResult:
        return AgentRunResult(
            status=status,
            response=None,
            tool_calls=(),
            tool_results=(),
            selection_result=selection,
            context_result=context_result,
            goal_completed=False,
            usage=TokenUsage(fixed_tokens, None, 0),
            stopped_reason=stopped_reason,
            interactions=(),
        )


def _append_tool_result(context: str, result: ToolResult) -> str:
    error = f" error={result.error}" if result.error else ""
    approval_id = result.output.get("approval_id")
    approval = f" approval_id={approval_id}" if isinstance(approval_id, str) else ""
    return (
        f"{context}\n\n[TOOL RESULT]\n"
        f"tool={result.tool_name} action={result.action} "
        f"success={str(result.success).lower()}{approval} "
        f"output={result.output}{error}"
    )


def usage_dict(usage: TokenUsage) -> dict[str, int | None]:
    """평가기가 읽는 usage 키."""
    return {
        "fixed_input_tokens": usage.fixed_input_tokens,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }
