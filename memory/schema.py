"""제약 보존과 상태 이력 관리를 위한 개별 메모리의 자료형.

전체 토큰 예산(max_token), 요청별 관련성, 최종 효용 점수는 선택기에서
관리한다. 이 모듈은 개별 기억의 값과 내부 일관성만 검증한다.
"""

from dataclasses import asdict, dataclass
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

    state_key는 세션 안에서 동일한 상태를 식별한다(예: report.current_file).
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
    importance: float = 0.5
    is_active: bool = True
    is_protected: bool = False
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    state_key: Optional[str] = None

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
        for name in ("supersedes", "superseded_by", "state_key"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be None or a non-empty string")
        if self.memory_id in (self.supersedes, self.superseded_by):
            raise ValueError("a memory cannot replace itself")
        if self.supersedes is not None and self.supersedes == self.superseded_by:
            raise ValueError("replacement links cannot form a cycle")
        if self.is_active and self.superseded_by is not None:
            raise ValueError("a superseded memory cannot be active")
        if self.memory_type == MemoryType.STATE and self.state_key is None:
            raise ValueError("state memories require state_key")
        if self.memory_type != MemoryType.STATE and self.state_key is not None:
            raise ValueError("state_key is only valid for state memories")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible record."""
        result = asdict(self)
        result["memory_type"] = self.memory_type.value
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryItem":
        """Construct and validate a record; unknown fields are rejected."""
        return cls(**dict(data))
