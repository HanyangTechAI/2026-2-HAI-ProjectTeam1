from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


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
    """Convert a raw query into QueryContext."""
    raise NotImplementedError