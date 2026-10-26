"""Memory Store 스켈레톤: docs/interfaces.md의 5절을 따른다.

저장, 버전 이력, 활성 버전 및 참조 일관성을 관리한다.
검색 순위, 효용 계산, 작업별 적용 여부 판단은 담당하지 않는다.
아래 메서드는 아직 구현되지 않았으며 호출하면 NotImplementedError가 발생한다.
"""

from __future__ import annotations

from dataclasses import replace

from .schema import MemoryItem, MemoryType


class MemoryStoreConsistencyError(RuntimeError):
    """내부 invariant 깨졌을 때 발생"""

class MemoryStore:
    """외부 모듈이 기억을 저장하고 조회하는 인터페이스.

    구현 시 동일 session_id와 memory_key의 활성 기억은 최대 하나여야 한다.
    MemoryItem은 불변 객체이므로 갱신 시 새 객체로 교체하고 과거 버전은
    보존한다. 저장 방식은 실제 구현 단계에서 결정한다.
    """
    
    def __init__(self) -> None:
        self._memories: dict[str, MemoryItem] = {}

    def insert(
        self, 
        memory: MemoryItem,
    ) -> MemoryItem:
        """기억을 저장하고 저장된 버전을 반환한다.

        기존 활성 버전을 대체할 경우 이전 기억의 is_active를 False로,
        valid_to를 새 기억의 valid_from으로 설정하고 양방향 대체 참조를
        연결한다. 이전 기억이 제약이면 is_protected도 False로 설정한다.
        ID 중복, 참조 일관성과 활성 버전의 유일성을 검증해야 한다.
        """
        if not isinstance(memory, MemoryItem):
            raise TypeError("memory must be a MemoryItem")
        
        if memory.memory_id in self._memories:
            raise ValueError(f"duplicate memory_id: {memory.memory_id}")
        
        if (memory.supersedes is not None or memory.superseded_by is not None) and memory.memory_key is None:
            raise ValueError("versioned memory requires memory_key")
        
        explicit_predecessor: MemoryItem | None = None
        
        if memory.supersedes is not None:
            explicit_predecessor = self._memories.get(memory.supersedes)
            
            if explicit_predecessor is None:
                raise ValueError(f"supersedes references unknown memory: {memory.supersedes}")
            
            self._validate_same_version_chain(
                explicit_predecessor, 
                memory,
            )
            
            if explicit_predecessor.superseded_by not in (None, memory.memory_id):
                raise MemoryStoreConsistencyError(f"{explicit_predecessor.memory_id} is already superseded by {explicit_predecessor.superseded_by}")
            
        if memory.memory_key is None:
            self._memories[memory.memory_id] = memory
            return memory
        
        active = self.find_active_by_key(
            memory.session_id, 
            memory.memory_key
        )
        
        predecessor: MemoryItem | None = None
        
        if memory.is_active:
            if active is not None:
                if explicit_predecessor is not None and active.memory_id != explicit_predecessor.memory_id:
                    raise MemoryStoreConsistencyError("new active memory supersedes a memory different from the current active version")
                
                predecessor = active
                
            elif explicit_predecessor is not None:
                predecessor = explicit_predecessor
                
        else:
            predecessor = explicit_predecessor
            
        stored_memory = memory
        
        if predecessor is not None:
            self._validate_version_time(
                predecessor, 
                memory,
            )
            
            old_memory = replace(
                predecessor,
                is_active=False,
                valid_to=memory.valid_from,
                superseded_by=memory.memory_id,
                is_protected=False if predecessor.memory_type == MemoryType.CONSTRAINT else predecessor.is_protected,
            )
            
            self._memories[old_memory.memory_id] = old_memory
            
            stored_memory = replace(
                memory, 
                supersedes=old_memory.memory_id
            )
            
        self._memories[stored_memory.memory_id] = stored_memory
        
        if stored_memory.memory_key is not None:
            self.find_active_by_key(
                stored_memory.session_id, 
                stored_memory.memory_key,
            )
            
        return stored_memory

    def get(
        self, 
        memory_id: str
    ) -> MemoryItem | None:
        """ID로 기억을 조회한다. 존재하지 않으면 None을 반환한다."""
        self._validate_non_empty_string(
            memory_id, 
            "memory_id",
        )
        
        return self._memories.get(memory_id)
    
    def find_active_by_key(
        self, 
        session_id: str, 
        memory_key: str,
    ) -> MemoryItem | None:
        """세션과 키에 해당하는 활성 기억을 반환한다. 없으면 None이다.

        활성 기억이 여러 개라면 임의로 선택하지 않고 일관성 오류로 처리한다.
        """
        self._validate_non_empty_string(
            session_id, 
            "session_id",
        )
        self._validate_non_empty_string(
            memory_key, 
            "memory_key",
        )

        active_memories = [
            memory
            for memory in self._memories.values()
            if memory.session_id == session_id and memory.memory_key == memory_key and memory.is_active
        ]

        if len(active_memories) > 1:
            ids = [
                memory.memory_id 
                for memory in active_memories
            ]
            raise MemoryStoreConsistencyError(f"multiple active memories found for session_id={session_id!r}, memory_key={memory_key!r}: {ids}")

        if not active_memories:
            return None

        return active_memories[0]

    def find_all_by_key(
        self, 
        session_id: str, 
        memory_key: str,
    ) -> list[MemoryItem]:
        """세션과 키에 해당하는 모든 버전을 valid_from 오름차순으로 반환한다.

        비활성 및 대체된 버전도 포함하며, 해당 기록이 없으면 빈 리스트이다.
        """
        self._validate_non_empty_string(
            session_id, 
            "session_id",
        )
        self._validate_non_empty_string(
            memory_key, 
            "memory_key",
        )

        memories = [
            memory
            for memory in self._memories.values()
            if memory.session_id == session_id and memory.memory_key == memory_key
        ]
        
        try:
            return sorted(
                memories, 
                key=lambda m: (
                    m.valid_from, 
                    m.turn_id, 
                    m.memory_id,
                ),
            )
            
        except TypeError as e:
            raise MemoryStoreConsistencyError("version chain contains incomparable timestamps") from e

    def find_active_protected_constraints(
        self, 
        session_id: str,
    ) -> list[MemoryItem]:
        """세션의 활성 상태이며 보호된 CONSTRAINT 기억을 반환한다.

        시간 필터를 적용하지 않는다. 작업별 적용 여부는 선택기가 판단한다.
        """
        self._validate_non_empty_string(
            session_id, 
            "session_id",
        )
        
        constraints = [
            memory
            for memory in self._memories.values()
            if memory.session_id == session_id and memory.memory_type == MemoryType.CONSTRAINT and memory.is_active and memory.is_protected
        ]
        
        return sorted(
            constraints, 
            key=lambda m: (
                m.turn_id, 
                m.memory_id,
            ),
        )

    def list_session_memories(
        self, 
        session_id: str,
    ) -> list[MemoryItem]:
        """검색 인덱스 구축이나 테스트를 위해 세션의 모든 기억을 반환한다."""
        self._validate_non_empty_string(
            session_id, 
            "session_id"
        )
        
        memories = [
            memory
            for memory in self._memories.values()
            if memory.session_id == session_id
        ]
        
        return sorted(
            memories, 
            key=lambda m: (
                m.turn_id, 
                m.memory_id,
            ),
        )

    @staticmethod
    def _validate_same_version_chain(
        old: MemoryItem, 
        new: MemoryItem,
    ) -> None:
        if old.session_id != new.session_id:
            raise ValueError("versioned memories must belong to the same session")
        
        if old.memory_key is None or new.memory_key is None or old.memory_key != new.memory_key:
            raise ValueError("versioned memories must have the same memory_key")

    @staticmethod
    def _validate_non_empty_string(
        value: str, 
        name: str,
    ) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        
    @staticmethod
    def _validate_version_time(
        old: MemoryItem, 
        new: MemoryItem,
    ) -> None:
        try:
            if new.valid_from < old.valid_from:
                raise ValueError("new version valid_from cannot precede the previous version valid_from")
            
        except TypeError as e:
            raise ValueError("version timestamps must use compatible timezone information") from e