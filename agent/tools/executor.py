"""ToolCall을 Email, File, Calendar, Task mock으로 보낸다."""

from __future__ import annotations

import inspect

from .calendar import CalendarTool
from .email import EmailTool
from .file import FileTool
from .task import TaskTool
from .types import ToolCall, ToolResult


ACTIONS = {
    "email": frozenset({"draft_email", "request_approval", "send_email"}),
    "file": frozenset({"find_file", "select_file", "delete_file"}),
    "calendar": frozenset({"create_event", "find_event"}),
    "task": frozenset({"create_task", "update_task"}),
}


class ToolExecutor:
    """네 가지 mock 툴의 진입점. 호출 이력은 평가에 넘길 수 있다."""

    def __init__(
        self,
        email: EmailTool | None = None,
        file: FileTool | None = None,
        calendar: CalendarTool | None = None,
        task: TaskTool | None = None,
    ) -> None:
        self.email = email or EmailTool()
        self.file = file or FileTool()
        self.calendar = calendar or CalendarTool()
        self.task = task or TaskTool()
        self.calls: list[ToolCall] = []
        self.results: list[ToolResult] = []

    def execute(self, call: ToolCall) -> ToolResult:
        """등록된 action만 실행한다. 알 수 없는 툴과 action은 실패 결과다."""
        self.calls.append(call)
        actions = ACTIONS.get(call.tool_name)
        if actions is None:
            result = ToolResult(False, call.tool_name, call.action, {}, "unknown tool")
        elif call.action not in actions:
            result = ToolResult(False, call.tool_name, call.action, {}, "unknown action")
        else:
            tool = getattr(self, call.tool_name)
            method = getattr(tool, call.action)
            try:
                inspect.signature(method).bind(**call.arguments)
            except TypeError as exc:
                result = ToolResult(False, call.tool_name, call.action, {}, str(exc))
            else:
                result = method(**call.arguments)
        self.results.append(result)
        return result

    def reset(self) -> None:
        """툴 상태와 호출 이력을 실험 시작 상태로 되돌린다."""
        self.email.reset()
        self.file.reset()
        self.calendar.reset()
        self.task.reset()
        self.calls.clear()
        self.results.clear()
