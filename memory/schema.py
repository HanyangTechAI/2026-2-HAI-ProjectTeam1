"""제약 보존과 상태 이력 관리를 위한 개별 메모리의 자료형.

전체 토큰 예산(max_token), 요청별 관련성, 최종 효용 점수는 선택기에서
관리한다. 이 모듈은 개별 기억의 값과 내부 일관성만 검증한다.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Any, Mapping, Optional


class MemoryType(str, Enum):
    """기억의 내용 유형. 보호 여부(is_protected)와는 별개의 속성이다."""

    CONSTRAINT = "constraint"
    STATE = "state"
    PREFERENCE = "preference"
    FACT = "fact"
    EXPERIENCE = "experience"


@dataclass(frozen=True)
class MemoryItem:
    """생성 후 직접 수정할 수 없는 기억 한 개의 스냅샷.

    문서의 Protected Memory는 활성 기억 중 is_protected=True인 항목,
    Current State는 활성 STATE 항목에 대응한다. 선호, 사실, 과거 경험은
    보호되지 않았다면 Flexible Memory로 선택할 수 있다.
    행동의 필수 제약은 생성하는 쪽에서 is_protected=True로 지정한다.
    보호는 선택 우선권이며, 명시적인 폐기나 대체를 금지하지 않는다.

    memory_key는 유형에 관계없이 세션 안에서 변경되는 동일 정보를
    식별한다(예: report.current_file, email.external.requires_approval).
    created_at은 생성 시점, valid_from과 valid_to는 정보의 유효 기간이다.
    유효 기간은 [valid_from, valid_to)이며 valid_to=None이면 종료 미정이다.
    시간은 datetime 또는 ISO 8601 문자열로 입력하고 내부에서는 datetime으로
    보관한다. 한 기록의 시간대 유무는 통일해야 하며, 시간대 없는 입력에
    임의의 시간대를 붙이지 않는다.
    실제 최신 버전 판별과 기억 사이의 참조 검증은 저장소의 책임이다.
    token_count는 외부 토크나이저가 계산한 비용이며, importance의 0~1
    범위는 구현상의 정규화 규칙이다. importance 자체가 최종 효용은 아니다.
    """

    memory_id: str
    session_id: str
    turn_id: int
    memory_type: MemoryType
    content: str
    token_count: int
    created_at: datetime
    valid_from: datetime
    valid_to: Optional[datetime] = None
    importance: float = 0.5
    is_active: bool = True
    is_protected: bool = False
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    memory_key: Optional[str] = None

    def __post_init__(self) -> None:
        """생성 시 값의 범위와 한 기록 안의 모순을 검사한다."""
        for name in ("memory_id", "session_id", "content"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("turn_id", "token_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("is_active", "is_protected"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if (type(self.importance) not in (int, float)
                or not isfinite(self.importance)
                or not 0 <= self.importance <= 1):
            raise ValueError("importance must be a finite number between 0 and 1")
        object.__setattr__(self, "memory_type", MemoryType(self.memory_type))
        for name in ("supersedes", "superseded_by", "memory_key"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be None or a non-empty string")
        if self.memory_id in (self.supersedes, self.superseded_by):
            raise ValueError("a memory cannot replace itself")
        if self.supersedes is not None and self.supersedes == self.superseded_by:
            raise ValueError("replacement links cannot form a cycle")
        if self.is_active and self.superseded_by is not None:
            raise ValueError("a superseded memory cannot be active")
        if self.memory_type == MemoryType.STATE and self.memory_key is None:
            raise ValueError("state memories require memory_key")

        for name in ("created_at", "valid_from", "valid_to"):
            value = getattr(self, name)
            if name == "valid_to" and value is None:
                continue
            if isinstance(value, str):
                try:
                    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError(f"{name} must be an ISO 8601 timestamp") from exc
            if not isinstance(value, datetime):
                raise ValueError(f"{name} must be a datetime or ISO 8601 timestamp")
            object.__setattr__(self, name, value)

        timestamps = (self.created_at, self.valid_from, self.valid_to)
        awareness = {value.utcoffset() is not None for value in timestamps if value is not None}
        if len(awareness) > 1:
            raise ValueError("timestamps must consistently include or omit timezone information")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to cannot precede valid_from")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        result = asdict(self)
        result["memory_type"] = self.memory_type.value
        for name in ("created_at", "valid_from", "valid_to"):
            value = getattr(self, name)
            result[name] = value.isoformat() if value is not None else None
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryItem":
        """Construct and validate a record; unknown fields are rejected."""
        return cls(**dict(data))
