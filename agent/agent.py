"""완성된 입력으로 행동 하나를 고르고 툴을 실행한다.

프롬프트를 조립하지 않는다. 토큰을 세지 않고, 예산 초과를 검사하지 않는다.
기억을 만들거나 저장소에 넣지 않는다. 한 번 호출에 행동은 하나다.
툴을 실행했으면 그 결과를 Interaction으로 돌려주고 멈춘다. 메모리 쪽이
그 Interaction을 정리하고 Context Builder가 다시 조립한 context로 다시 부른다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .interaction import Interaction, interactions_from_tool_results
from .tools.executor import ToolExecutor
from .tools.types import ToolCall, ToolResult

if TYPE_CHECKING:
    from .planner import Planner

GoalChecker = Callable[[str | None, tuple[ToolCall, ...], tuple[ToolResult, ...]], bool]


@dataclass(frozen=True)
class AgentAction:
    """plan의 한 단계. 툴을 부르지 않으면 tool_name과 action은 None이다."""

    tool_name: str | None
    action: str | None
    arguments: dict
    text_response: str | None


@dataclass(frozen=True)
class ActResult:
    """행동 하나의 결과. 토큰 수는 포함하지 않는다.

    stopped_reason은 completed, tool_called, tool_failure 중 하나다.
    tool_called이면 다시 조립한 context로 다시 불러야 한다.
    """

    response: str | None
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    stopped_reason: str
    interactions: tuple[Interaction, ...]


@dataclass(frozen=True)
class AgentRunResult:
    """run_task의 결과. 기억 선택과 프롬프트 조립 결과는 포함하지 않는다."""

    response: str | None
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    goal_completed: bool
    stopped_reason: str
    interactions: tuple[Interaction, ...]


class Agent:
    """완성된 입력을 받아 행동 하나를 고르고 툴을 실행하는 실행기.

    기억 선택, 프롬프트 조립, 토큰 계산, 예산 검사는 이 클래스 밖에 둔다.
    goal_checker가 없으면 goal_completed는 False다.
    """

    def __init__(
        self,
        *,
        executor: ToolExecutor | None = None,
        planner: Planner | None = None,
        goal_checker: GoalChecker | None = None,
        current_turn: int = 0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if planner is None:
            from .planner import Planner as PlannerImpl

            planner = PlannerImpl()
        self.executor = executor or ToolExecutor()
        self.planner = planner
        self.goal_checker = goal_checker
        self.current_turn = current_turn
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def plan(self, context: str) -> AgentAction:
        """완성된 입력을 planner에 넘긴다."""
        return self.planner.plan(context)

    def run_task(self, context: str, session_id: str) -> AgentRunResult:
        """Context Builder가 만든 입력으로 행동 하나를 실행한다."""
        acted = self.act(context, session_id=session_id)
        goal_completed = (
            acted.stopped_reason == "completed"
            and self.goal_checker is not None
            and self.goal_checker(acted.response, acted.tool_calls, acted.tool_results)
        )
        return AgentRunResult(
            response=acted.response,
            tool_calls=acted.tool_calls,
            tool_results=acted.tool_results,
            goal_completed=goal_completed,
            stopped_reason=acted.stopped_reason,
            interactions=acted.interactions,
        )

    def act(self, context: str, *, session_id: str) -> ActResult:
        """plan 한 번과 툴 실행 한 번을 한다.

        툴을 실행하면 current_turn을 Interaction 수만큼 올린다.
        다음 호출의 Interaction은 그 다음 차례부터 번호를 받는다.
        """
        action = self.plan(context)
        if action.tool_name is None and action.action is None:
            return ActResult(action.text_response, (), (), "completed", ())
        if action.tool_name is None or action.action is None:
            return ActResult(None, (), (), "tool_failure", ())

        call = ToolCall(action.tool_name, action.action, dict(action.arguments))
        result = self.executor.execute(call)
        interactions = interactions_from_tool_results(
            session_id,
            self.current_turn,
            (result,),
            self.clock(),
        )
        self.current_turn += len(interactions)
        return ActResult(
            response=None,
            tool_calls=(call,),
            tool_results=(result,),
            stopped_reason="tool_called" if result.success else "tool_failure",
            interactions=interactions,
        )
