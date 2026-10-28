"""툴 결과를 Interaction으로 바꾼다.

저장소에 넣지 않는다. Interaction을 받아 정리하는 일은 메모리 쪽이 맡는다.
"""

from __future__ import annotations

import json
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


def format_tool_result(result: ToolResult) -> str:
    """툴 결과 한 줄. Interaction.content로 쓴다. Planner도 이 형식을 읽는다."""
    output = json.dumps(result.output, ensure_ascii=False, sort_keys=True, default=str)
    line = (
        f"{result.tool_name}.{result.action} "
        f"success={str(result.success).lower()} output={output}"
    )
    if result.error:
        line += f" error={result.error}"
    return line


def interactions_from_tool_results(
    session_id: str,
    turn_id: int,
    results: tuple[ToolResult, ...] | list[ToolResult],
    timestamp: datetime,
) -> tuple[Interaction, ...]:
    """각 툴 결과를 source가 tool인 Interaction으로 만든다.

    turn_id는 툴 호출 직전 차례다. n번째 결과는 turn_id + n이다.
    """
    return tuple(
        Interaction(
            session_id=session_id,
            turn_id=turn_id + offset,
            content=format_tool_result(result),
            source="tool",
            timestamp=timestamp,
        )
        for offset, result in enumerate(results, start=1)
    )
