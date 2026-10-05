"""Task mock. 작업 생성과 상태 변경만 기록한다."""

from __future__ import annotations

from .types import ToolResult


class TaskTool:
    """열린 작업 목록."""

    def __init__(self) -> None:
        self.tasks: list[dict] = []

    def create_task(self, title: str) -> ToolResult:
        """status가 open인 작업을 추가한다."""
        if not isinstance(title, str) or not title.strip():
            return ToolResult(False, "task", "create_task", {}, "title must be a non-empty string")
        task = {
            "task_id": f"task_{len(self.tasks) + 1:03d}",
            "title": title,
            "status": "open",
        }
        self.tasks.append(task)
        return ToolResult(True, "task", "create_task", dict(task))

    def update_task(
        self,
        task_id: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        owner: str | None = None,
    ) -> ToolResult:
        """작업의 상태, 담당자, 소유자를 기록한다.

        시나리오는 task_id 없이 assignee만 넘길 수 있다. 금지된 owner 변경도
        툴이 막지 않고 기록한다.
        """
        fields = {"task_id": task_id, "status": status, "assignee": assignee, "owner": owner}
        for name, value in fields.items():
            if value is not None and (not isinstance(value, str) or not value.strip()):
                return ToolResult(False, "task", "update_task", {}, f"{name} must be a non-empty string or omitted")
        if status is None and assignee is None and owner is None:
            return ToolResult(False, "task", "update_task", {}, "status, assignee, or owner is required")
        task = next((item for item in self.tasks if item["task_id"] == task_id), None) if task_id else None
        if task is None:
            task = {
                "task_id": task_id or f"task_{len(self.tasks) + 1:03d}",
                "title": "",
                "status": status or "open",
            }
            self.tasks.append(task)
        if status is not None:
            task["status"] = status
        if assignee is not None:
            task["assignee"] = assignee
        if owner is not None:
            task["owner"] = owner
        return ToolResult(True, "task", "update_task", dict(task))

    def reset(self) -> None:
        """작업을 모두 지운다."""
        self.tasks.clear()
