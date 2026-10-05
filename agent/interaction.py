"""툴 결과를 기억 파이프라인에 넘길 Interaction으로 바꾼다.

저장소 삽입은 이 모듈이 하지 않는다. Memory Analyzer가 받을 입력을 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .tools.types import ToolResult


@dataclass(frozen=True)
class Interaction:
    """docs/interfaces.md 3.3절의 공통 입력."""

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
    interactions = []
    for offset, result in enumerate(results, start=1):
        error = f" error={result.error}" if result.error else ""
        interactions.append(Interaction(
            session_id=session_id,
            turn_id=turn_id + offset,
            content=(
                f"{result.tool_name}.{result.action} "
                f"success={str(result.success).lower()} "
                f"output={result.output}{error}"
            ),
            source="tool",
            timestamp=timestamp,
        ))
    return tuple(interactions)
