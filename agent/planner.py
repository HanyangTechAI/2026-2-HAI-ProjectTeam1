"""다음 행동 하나를 고르는 자리.

LLM이 정해지기 전까지 쓰는 결정적 규칙 planner다. Context Builder가 만든
문자열의 섹션만 읽는다. 이전 툴 결과는 Interaction.content와 같은 형식의 줄이면
어느 섹션에 있든 읽는다. 같은 입력에는 항상 같은 행동을 돌려준다.
프롬프트를 조립하거나 툴을 실행하지 않는다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime

from .agent import AgentAction

SECTION = re.compile(r"^\[([A-Z][A-Z ]*)\]$")
TOOL_RESULT = re.compile(r"^(\w+)\.(\w+) success=(true|false) output=")
FILENAME = re.compile(
    r"[\w.-]+\.(?:pdf|docx?|xlsx?|pptx?|hwp|txt|csv|zip)\b",
    re.IGNORECASE | re.ASCII,
)
EMAIL_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.ASCII)
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})", re.ASCII)
MEMBER = re.compile(r"\bmember_\w+\b", re.ASCII)
GRANTED_APPROVAL = re.compile(r"\bapproved_\d+\b", re.ASCII)
EARLIEST_HOUR = re.compile(r"오전\s*(\d{1,2})시\s*이전")


class Planner:
    """규칙으로 행동을 고르는 계획기. LLM planner로 바꿔 끼울 자리다.

    recipient_aliases는 과제의 호칭을 주소로 바꾸는 표다.
    예: {"교수님": "professor@example.test"}. 표에 없는 호칭의 주소는 만들지 않는다.
    """

    def __init__(self, recipient_aliases: Mapping[str, str] | None = None) -> None:
        self.recipient_aliases = dict(recipient_aliases or {})

    def plan(self, context: str) -> AgentAction:
        """완성된 입력에서 다음 행동 하나를 고른다."""
        view = _ContextView(context)
        task = view.task
        lowered = task.lower()
        if any(word in task for word in ("메일", "보내", "발송")) or "email" in lowered:
            return self._email(view)
        if any(word in task for word in ("일정", "회의")) or "calendar" in lowered:
            return _calendar(view)
        if any(word in task for word in ("담당자", "작업")) or "task" in lowered:
            return _task(view)
        if "삭제" in task and "금지" not in task:
            return _delete(view)
        return _find_file(view)

    def _email(self, view: _ContextView) -> AgentAction:
        if view.last_ok("email", "send_email") is not None:
            return _say("메일을 보냈습니다.")
        recipient = self._recipient(view)
        if recipient is None:
            return _say("수신자 주소를 찾지 못해 메일을 보내지 않았습니다.")
        arguments = {"recipient": recipient}
        attachment = _first(FILENAME, view.states, [view.task], view.memories)
        if attachment is not None:
            arguments["attachment"] = attachment

        if self._needs_approval(view.protected, recipient):
            granted = _first(GRANTED_APPROVAL, view.protected, view.states, view.memories)
            if granted is None:
                pending = view.last_ok("email", "request_approval")
                if pending is not None:
                    return _say(f"승인을 기다리고 있습니다: {pending.get('approval_id')}")
                return _call("email", "request_approval", arguments)
            arguments["approval_id"] = granted
        return _call("email", "send_email", arguments)

    def _recipient(self, view: _ContextView) -> str | None:
        address = _first(EMAIL_ADDRESS, [view.task])
        if address is not None:
            return address
        for alias, alias_address in self.recipient_aliases.items():
            if alias in view.task:
                return alias_address
        return _first(EMAIL_ADDRESS, view.states)

    def _needs_approval(self, protected: list[str], recipient: str) -> bool:
        required = False
        exempt = False
        for line in protected:
            if "승인" not in line:
                continue
            if "없이" in line and "금지" not in line:
                exempt = exempt or self._exemption_applies(line, recipient)
            else:
                required = True
        return required and not exempt

    def _exemption_applies(self, line: str, recipient: str) -> bool:
        targeted = "에게" in line or "께" in line
        if not targeted:
            return True
        return any(
            alias in line and address == recipient
            for alias, address in self.recipient_aliases.items()
        ) or recipient in line


class _ContextView:
    """Context 문자열을 섹션별 줄로 나눈 것."""

    def __init__(self, context: str) -> None:
        self.sections: dict[str, list[str]] = {}
        self.tool_results: list[dict] = []
        title: str | None = None
        for raw in context.splitlines():
            line = raw.strip()
            match = SECTION.match(line)
            if match:
                title = match.group(1)
                self.sections.setdefault(title, [])
                continue
            if title is None or not line:
                continue
            content = line.removeprefix("- ").strip()
            parsed = _parse_tool_result(content)
            if parsed is not None:
                self.tool_results.append(parsed)
                continue
            self.sections[title].append(content)

    @property
    def task(self) -> str:
        return " ".join(self.sections.get("CURRENT TASK", []))

    @property
    def protected(self) -> list[str]:
        return self.sections.get("PROTECTED CONSTRAINTS", [])

    @property
    def states(self) -> list[str]:
        return self.sections.get("STATE", [])

    @property
    def memories(self) -> list[str]:
        return self.sections.get("RELEVANT MEMORY", [])

    def last_ok(self, tool_name: str, action: str) -> dict | None:
        """가장 최근에 성공한 해당 툴 호출의 output."""
        for result in reversed(self.tool_results):
            if result["tool_name"] == tool_name and result["action"] == action and result["success"]:
                return result["output"]
        return None


def _parse_tool_result(line: str) -> dict | None:
    match = TOOL_RESULT.match(line)
    if match is None:
        return None
    try:
        output, _ = json.JSONDecoder().raw_decode(line[match.end():])
    except json.JSONDecodeError:
        output = {}
    return {
        "tool_name": match.group(1),
        "action": match.group(2),
        "success": match.group(3) == "true",
        "output": output if isinstance(output, dict) else {},
    }


def _calendar(view: _ContextView) -> AgentAction:
    created = view.last_ok("calendar", "create_event")
    if created is not None:
        return _say(f"일정을 만들었습니다: {created.get('start_time')}")
    start = _first(TIMESTAMP, view.states, [view.task], view.memories)
    if start is None:
        return _say("일정 시각을 찾지 못했습니다.")
    for line in view.protected:
        limit = EARLIEST_HOUR.search(line)
        if limit and datetime.fromisoformat(start.replace("Z", "+00:00")).hour < int(limit.group(1)):
            return _say(f"제약 때문에 일정을 만들지 않았습니다: {line}")
    return _call("calendar", "create_event", {"start_time": start})


def _task(view: _ContextView) -> AgentAction:
    updated = view.last_ok("task", "update_task")
    if updated is not None:
        return _say(f"담당자를 {updated.get('assignee')}로 지정했습니다.")
    assignee = _first(MEMBER, view.states, [view.task], view.memories)
    if assignee is None:
        return _say("담당자를 찾지 못했습니다.")
    return _call("task", "update_task", {"assignee": assignee})


def _delete(view: _ContextView) -> AgentAction:
    deleted = view.last_ok("file", "delete_file")
    if deleted is not None:
        return _say(f"{deleted.get('filename')}을 삭제했습니다.")
    filename = _first(FILENAME, [view.task], view.states, view.memories)
    if filename is None:
        return _say("삭제할 파일을 찾지 못했습니다.")
    for line in view.protected:
        if "삭제" in line and any(word in line for word in ("않", "금지", "말")):
            return _say(f"제약 때문에 삭제하지 않았습니다: {line}")
    return _call("file", "delete_file", {"filename": filename})


def _find_file(view: _ContextView) -> AgentAction:
    found = view.last_ok("file", "find_file")
    if found is not None:
        status = "있습니다" if found.get("found") else "목록에 없습니다"
        return _say(f"{found.get('filename')} 파일이 {status}.")
    filename = _first(FILENAME, view.states, [view.task], view.memories)
    if filename is None:
        return _say("처리할 행동을 찾지 못했습니다.")
    return _call("file", "find_file", {"filename": filename})


def _first(pattern: re.Pattern[str], *groups: list[str]) -> str | None:
    for lines in groups:
        for line in lines:
            match = pattern.search(line)
            if match:
                return match.group(0)
    return None


def _call(tool_name: str, action: str, arguments: dict) -> AgentAction:
    return AgentAction(tool_name, action, arguments, None)


def _say(text: str) -> AgentAction:
    return AgentAction(None, None, {}, text)
