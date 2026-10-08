"""툴 결과를 Interaction으로 바꿀 자리.

저장소에 넣지 않는다. 변환은 아직 구현하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .tools.types import ToolResult


@dataclass(frozen=True)
class Interaction:
    """기억 파이프라인이 받을 공통 입력의 칸."""

    session_id: str
    turn_id: int
    content: str
    source: str
    timestamp: datetime


def interactions_from_tool_results(
    session_id: str,
    turn_id: int,
    results: tuple[ToolResult, ...] | list[ToolResult],
    timestamp: datetime,
) -> tuple[Interaction, ...]:
    """각 툴 결과를 source가 tool인 Interaction으로 만든다."""
    raise NotImplementedError
