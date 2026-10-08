"""Agent 실행 스켈레톤.

프롬프트를 조립하지 않는다. 토큰을 세지 않고, 예산 초과를 검사하지 않는다.
기억을 만들거나 저장소에 넣지 않는다. 완성된 입력을 받아 다음 행동을 고르고
툴을 실행하는 자리는 여기 두되, 그 동작은 아직 구현하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .interaction import Interaction
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
    """계획과 툴 실행이 남길 결과. 토큰 수는 포함하지 않는다."""

    response: str | None
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    stopped_reason: str
    interactions: tuple[Interaction, ...]


@dataclass(frozen=True)
class AgentRunResult:
    """run_task가 남길 결과. 기억 선택과 프롬프트 조립 결과는 포함하지 않는다."""

    response: str | None
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    goal_completed: bool
    stopped_reason: str
    interactions: tuple[Interaction, ...]


class Agent:
    """완성된 입력을 받아 행동을 고르고 툴을 실행할 실행기.

    기억 선택, 프롬프트 조립, 토큰 계산, 예산 검사는 이 클래스 밖에 둔다.
    """

    def __init__(
        self,
        *,
        executor: ToolExecutor | None = None,
        planner: Planner | None = None,
        goal_checker: GoalChecker | None = None,
        current_turn: int = 0,
        max_steps: int = 8,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        if planner is None:
            from .planner import Planner as PlannerImpl

            planner = PlannerImpl()
        self.executor = executor or ToolExecutor()
        self.planner = planner
        self.goal_checker = goal_checker
        self.current_turn = current_turn
        self.max_steps = max_steps

    def plan(self, context: str) -> AgentAction:
        """완성된 입력을 planner에 넘긴다."""
        return self.planner.plan(context)

    def run_task(self, task: str, session_id: str) -> AgentRunResult:
        """과제 한 번을 실행한다. 예산과 프롬프트는 받지 않는다."""
        raise NotImplementedError

    def act(self, context: str, *, session_id: str) -> ActResult:
        """이미 만들어진 입력으로 계획과 툴 실행만 한다."""
        raise NotImplementedError
