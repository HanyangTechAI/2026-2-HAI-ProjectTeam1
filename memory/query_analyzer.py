from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import calendar
import re


class TemporalIntent(str, Enum):
    CURRENT = "current"
    HISTORICAL = "historical"
    RELATIVE_TIME = "relative_time"
    UNSPECIFIED = "unspecified"


@dataclass(frozen=True)
class QueryContext:
    query: str
    temporal_intent: TemporalIntent

    target_time: datetime | None = None
    target_range: tuple[datetime, datetime] | None = None

    entities: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()

    current_turn: int = 0
    current_time: datetime | None = None


def analyze_query(
    query: str,
    current_turn: int,
    current_time: datetime,
) -> QueryContext:
    """문서의 시간 표현과 도구 어휘를 규칙 기반으로 분석한다.

    상대 시각은 시스템 시계가 아닌 current_time을 기준으로 계산한다.
    지원하지 않는 명시적 시간 표현은 현재 요청으로 오인하지 않고 거부한다.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if type(current_turn) is not int or current_turn < 0:
        raise ValueError("current_turn must be a non-negative integer")
    if not isinstance(current_time, datetime):
        raise TypeError("current_time must be a datetime")

    text = query.casefold()
    intent = TemporalIntent.UNSPECIFIED
    target = None
    period = None
    absolute = re.search(
        r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?",
        query,
    )
    month = re.search(r"(?:(\d{4})년\s*)?(\d{1,2})월(?:\s*(\d{1,2})일)?", text)
    relative = re.search(r"(\d+|한|두|세|네|일|이|삼)\s*(달|개월|년|주|일|시간)\s*(전|후)", text)
    # 명시된 정보 시점이 행동 시점의 '지금'보다 우선한다(EC-09).
    if absolute:
        target = datetime.fromisoformat(absolute.group().replace("Z", "+00:00"))
        if target.tzinfo is None:
            target = target.replace(tzinfo=current_time.tzinfo)
        intent = TemporalIntent.CURRENT if target == current_time else TemporalIntent.HISTORICAL
    elif month:
        year = int(month[1]) if month[1] else current_time.year
        start = datetime(year, int(month[2]), int(month[3] or 1), tzinfo=current_time.tzinfo)
        if month[3]:
            target = start
        else:
            end = _shift_months(start, 1)
            period = (start, end)
        intent = TemporalIntent.HISTORICAL
    elif re.search(r"(?<!\d)(\d{4})년", text):
        year = int(re.search(r"(?<!\d)(\d{4})년", text)[1])
        period = (datetime(year, 1, 1, tzinfo=current_time.tzinfo),
                  datetime(year + 1, 1, 1, tzinfo=current_time.tzinfo))
        intent = TemporalIntent.HISTORICAL
    elif relative:
        numbers = {"한": 1, "두": 2, "세": 3, "네": 4, "일": 1, "이": 2, "삼": 3}
        count = int(relative[1]) if relative[1].isdigit() else numbers[relative[1]]
        count *= -1 if relative[3] == "전" else 1
        unit = relative[2]
        if unit in ("달", "개월", "년"):
            target = _shift_months(current_time, count * (12 if unit == "년" else 1))
        else:
            target = current_time + timedelta(**{{"주": "weeks", "일": "days", "시간": "hours"}[unit]: count})
        intent = TemporalIntent.RELATIVE_TIME
    elif any(word in text for word in ("지난달", "지난 달", "지난주", "지난 주", "작년")):
        midnight = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        if "작년" in text:
            start = midnight.replace(year=midnight.year - 1, month=1, day=1)
            end = start.replace(year=start.year + 1)
        elif "달" in text:
            end = midnight.replace(day=1)
            start = _shift_months(end, -1)
        else:
            end = midnight - timedelta(days=midnight.weekday())
            start = end - timedelta(weeks=1)
        period = (start, end)
        intent = TemporalIntent.RELATIVE_TIME
    elif any(word in text for word in ("어제", "오늘", "내일")):
        word = next(word for word in ("어제", "오늘", "내일") if word in text)
        target = current_time + timedelta(days={"어제": -1, "오늘": 0, "내일": 1}[word])
        intent = TemporalIntent.RELATIVE_TIME
    elif re.search(r"지난|이전|당시|과거|예전|\b(?:ago|yesterday|last)\b", text):
        raise ValueError("지원하지 않는 시간 표현입니다. 구체적인 날짜나 시각이 필요합니다.")
    elif re.search(r"지금|현재|최신|\b(?:current|now|latest)\b", text):
        intent = TemporalIntent.CURRENT

    entities = tuple(dict.fromkeys((*_specific_entities(query), *_concepts(query))))
    tools = list(_detect_tools(text))
    if "send" in _actions(text) and (set(tools) & {"email", "file"} or _recipients(query)):
        if "email" not in tools:
            tools.append("email")
    return QueryContext(
        query=query, temporal_intent=intent, target_time=target, target_range=period,
        entities=entities, required_tools=tuple(tools),
        current_turn=current_turn, current_time=current_time,
    )


def _shift_months(value: datetime, months: int) -> datetime:
    """달력 기준 월 이동. 존재하지 않는 일자는 해당 월의 말일로 제한한다."""
    year, month = divmod(value.year * 12 + value.month - 1 + months, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def _detect_tools(text: str) -> tuple[str, ...]:
    """문서의 네 도구 이름으로 정규화한다."""
    patterns = {
        "email": r"이메일|메일|\b(?:email|mail)\b",
        "file": r"보고서|파일|문서|\b(?:file|report|document)\b|\.(?:pdf|docx|txt|csv)\b",
        "calendar": r"일정|회의|달력|\b(?:calendar|schedule|meeting)\b",
        "task": r"작업|할\s*일|\b(?:task|todo)\b",
    }
    return tuple(tool for tool, pattern in patterns.items() if re.search(pattern, text.casefold(), re.ASCII))


# 문서의 Entity/Tool/Action 신호를 위한 V1 어휘. 자유 문장 전체의 의미를
# 추론하는 모델은 아니며, 날짜 기본값은 current_time의 연도/시간대를 따른다.
_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
_FILE = r"[\w.-]+\.(?:pdf|docx?|xlsx?|pptx?|hwp|txt|csv|zip)\b"
_CONCEPTS = {
    "보고서": r"보고서|\breport\b|report_",
    "파일": r"파일|\bfile\b",
    "문서": r"문서|\bdocument\b",
    "이메일": r"이메일|메일|\b(?:email|mail)\b",
    "일정": r"일정|달력|\b(?:calendar|schedule)\b",
    "회의": r"회의|\bmeeting\b",
    "작업": r"작업|할\s*일|\b(?:task|todo)\b",
    "담당자": r"담당자|소유자|\b(?:assignee|owner|recipient|contact)\b",
    "계약서": r"계약서|\bcontract\b|contract_",
}


def _concepts(text: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in _CONCEPTS.items() if re.search(pattern, text.casefold()))


def _recipients(text: str) -> tuple[str, ...]:
    """명시된 주소 또는 '대상에게/께'만 추출한다. 호칭→주소를 추측하지 않는다."""
    addresses = re.findall(_EMAIL, text.casefold(), flags=re.ASCII)
    stripped = re.sub(_EMAIL, " ", text.casefold(), flags=re.ASCII)
    names = re.findall(r"([가-힣A-Za-z0-9_-]+?)(?:에게|한테|께)", stripped)
    roles = re.findall(r"교수님|고객|팀장|담당자", stripped)
    return tuple(dict.fromkeys((*addresses, *names, *roles)))


def _specific_entities(text: str) -> tuple[str, ...]:
    addresses = re.findall(_EMAIL, text.casefold(), flags=re.ASCII)
    stripped = re.sub(_EMAIL, " ", text, flags=re.ASCII)
    files = re.findall(_FILE, stripped, flags=re.IGNORECASE | re.ASCII)
    names = re.findall(r"[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9_-]+", stripped)
    return tuple(dict.fromkeys((*addresses, *files, *names, *_recipients(text))))


def _actions(text: str) -> tuple[str, ...]:
    patterns = {
        "send": r"발송|보내|전송|\b(?:send|sending)\b",
        "delete": r"삭제|지워|\b(?:delete|remove)\b",
        "update": r"수정|변경|지정|\b(?:update|edit|modify|assign)\b",
        "create": r"생성|만들|등록|\b(?:create|add)\b",
    }
    return tuple(action for action, pattern in patterns.items() if re.search(pattern, text.casefold()))
