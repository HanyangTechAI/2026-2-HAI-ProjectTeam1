"""Memory Store 스켈레톤: docs/interfaces.md의 5절을 따른다.

저장, 버전 이력, 활성 버전 및 참조 일관성을 관리한다.
검색 순위, 효용 계산, 작업별 적용 여부 판단은 담당하지 않는다.
아래 메서드는 아직 구현되지 않았으며 호출하면 NotImplementedError가 발생한다.
"""

from .schema import MemoryItem


class MemoryStore:
    """외부 모듈이 기억을 저장하고 조회하는 인터페이스.

    구현 시 동일 session_id와 memory_key의 활성 기억은 최대 하나여야 한다.
    MemoryItem은 불변 객체이므로 갱신 시 새 객체로 교체하고 과거 버전은
    보존한다. 저장 방식은 실제 구현 단계에서 결정한다.
    """

    def insert(self, memory: MemoryItem) -> MemoryItem:
        """기억을 저장하고 저장된 버전을 반환한다.

        기존 활성 버전을 대체할 경우 이전 기억의 is_active를 False로,
        valid_to를 새 기억의 valid_from으로 설정하고 양방향 대체 참조를
        연결한다. 이전 기억이 제약이면 is_protected도 False로 설정한다.
        ID 중복, 참조 일관성과 활성 버전의 유일성을 검증해야 한다.
        """
        raise NotImplementedError

    def get(self, memory_id: str) -> MemoryItem | None:
        """ID로 기억을 조회한다. 존재하지 않으면 None을 반환한다."""
        raise NotImplementedError

    def find_active_by_key(
        self, session_id: str, memory_key: str
    ) -> MemoryItem | None:
        """세션과 키에 해당하는 활성 기억을 반환한다. 없으면 None이다.

        활성 기억이 여러 개라면 임의로 선택하지 않고 일관성 오류로 처리한다.
        """
        raise NotImplementedError

    def find_all_by_key(
        self, session_id: str, memory_key: str
    ) -> list[MemoryItem]:
        """세션과 키에 해당하는 모든 버전을 valid_from 오름차순으로 반환한다.

        비활성 및 대체된 버전도 포함하며, 해당 기록이 없으면 빈 리스트이다.
        """
        raise NotImplementedError

    def find_active_protected_constraints(
        self, session_id: str
    ) -> list[MemoryItem]:
        """세션의 활성 상태이며 보호된 CONSTRAINT 기억을 반환한다.

        시간 필터를 적용하지 않는다. 작업별 적용 여부는 선택기가 판단한다.
        """
        raise NotImplementedError

    def list_session_memories(self, session_id: str) -> list[MemoryItem]:
        """검색 인덱스 구축이나 테스트를 위해 세션의 모든 기억을 반환한다."""
        raise NotImplementedError
