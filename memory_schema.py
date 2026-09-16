"""Memory data structures for constraint-preserving, budget-aware agent memory.

Token counts use tiktoken's ``cl100k_base`` encoding so that budget arithmetic
matches typical LLM context windows (GPT-family tokenizers).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from uuid import uuid4

import tiktoken

# Cached encoder: constructing tiktoken encodings is relatively expensive.
_ENCODER = tiktoken.get_encoding("cl100k_base")


class MemoryType(str, Enum):
    """Role of a memory record in the context budget.

    PROTECTED: hard constraints that must always occupy the prompt (cost C_P).
    CURRENT_STATE: versioned facts; only ACTIVE records are mandatory.
    FLEXIBLE: optional evidence selected under B_flex = B - C_P.
    """

    PROTECTED = "PROTECTED"
    CURRENT_STATE = "CURRENT_STATE"
    FLEXIBLE = "FLEXIBLE"


class ValidStatus(str, Enum):
    """Lifecycle of a versioned memory record."""

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"


def count_tokens(text: str) -> int:
    """Return the ``cl100k_base`` token length of ``text``."""

    return len(_ENCODER.encode(text))


@dataclass
class MemoryRecord:
    """A single unit of long-horizon agent memory."""

    id: str
    memory_type: MemoryType
    content: str
    source_turn: int
    scope: str
    valid_status: ValidStatus
    version: int
    token_count: int

    @classmethod
    def create(
        cls,
        memory_type: MemoryType,
        content: str,
        source_turn: int,
        scope: str = "global",
        valid_status: ValidStatus = ValidStatus.ACTIVE,
        version: int = 1,
        memory_id: Optional[str] = None,
    ) -> MemoryRecord:
        """Build a record and automatically fill ``token_count`` via tiktoken."""

        return cls(
            id=memory_id or str(uuid4()),
            memory_type=memory_type,
            content=content,
            source_turn=source_turn,
            scope=scope,
            valid_status=valid_status,
            version=version,
            token_count=count_tokens(content),
        )

    def is_mandatory(self) -> bool:
        """True if this record must be included in C_P (protected / active state)."""

        if self.memory_type == MemoryType.PROTECTED:
            return self.valid_status == ValidStatus.ACTIVE
        if self.memory_type == MemoryType.CURRENT_STATE:
            return self.valid_status == ValidStatus.ACTIVE
        return False
