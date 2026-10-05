"""Tool Call과 Tool Result의 공통 자료형.

docs/interfaces.md 19절. 툴 모듈과 Agent가 이 형식만으로 통신한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolCall:
    """한 번의 툴 호출.

    tool_name은 email, file, calendar, task 중 하나다.
    action은 해당 툴의 함수 이름이다.
    """

    tool_name: str
    action: str
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """툴 실행 결과. output은 JSON으로 직렬화할 수 있는 값만 담는다."""

    success: bool
    tool_name: str
    action: str
    output: dict
    error: str | None = None
