"""다음 행동 하나를 고르는 자리.

완성된 입력만 받는다. 프롬프트를 조립하지 않고, 토큰을 세지 않으며,
툴을 실행하지 않는다. 모델 호출은 아직 연결하지 않는다.
"""

from __future__ import annotations

from .agent import AgentAction


class Planner:
    """LLM이 붙을 계획기. 지금은 행동을 고르지 않는다."""

    def plan(self, context: str) -> AgentAction:
        """완성된 입력에서 다음 행동 하나를 고른다."""
        raise NotImplementedError
