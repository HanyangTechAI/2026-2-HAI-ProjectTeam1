"""Calendar mock. 일정은 메모리에만 저장한다."""

from __future__ import annotations

from datetime import datetime, timedelta

from .types import ToolResult


class CalendarTool:
    """생성한 일정의 목록."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def create_event(
        self,
        start_time: datetime,
        title: str | None = None,
        end_time: datetime | None = None,
    ) -> ToolResult:
        """timezone-aware 시작 시각으로 일정을 만든다.

        시나리오 정답은 start_time만 넘길 수 있다. 제목이 없으면 event,
        종료 시각이 없으면 시작 한 시간 뒤를 쓴다.
        """
        if title is None:
            title = "event"
        if not isinstance(title, str) or not title.strip():
            return _fail("create_event", "title must be a non-empty string or omitted")
        try:
            start = _parse_time(start_time)
            end = _parse_time(end_time) if end_time is not None else start + timedelta(hours=1)
        except ValueError as exc:
            return _fail("create_event", str(exc))
        if end <= start:
            return _fail("create_event", "end_time must be later than start_time")
        event = {
            "event_id": f"event_{len(self.events) + 1:03d}",
            "title": title,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
        }
        self.events.append(event)
        return ToolResult(True, "calendar", "create_event", event)

    def find_event(self, query: str) -> ToolResult:
        """제목에 query가 포함된 일정을 반환한다."""
        if not isinstance(query, str) or not query.strip():
            return _fail("find_event", "query must be a non-empty string")
        matches = [event for event in self.events if query.casefold() in event["title"].casefold()]
        return ToolResult(True, "calendar", "find_event", {"query": query, "events": matches})

    def reset(self) -> None:
        """일정을 모두 지운다."""
        self.events.clear()


def _parse_time(value: datetime | str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("start_time and end_time must be ISO 8601 timestamps") from exc
    if not isinstance(value, datetime):
        raise ValueError("start_time and end_time must be timezone-aware datetimes")
    if value.utcoffset() is None:
        raise ValueError("start_time and end_time must include a timezone")
    return value


def _fail(action: str, error: str) -> ToolResult:
    return ToolResult(False, "calendar", action, {}, error)
