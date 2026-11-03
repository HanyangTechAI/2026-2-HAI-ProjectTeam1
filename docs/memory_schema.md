# Memory schema

`memory/schema.py`의 `MemoryItem`은 생성 후 수정할 수 없는 단일 Memory snapshot이다. 다음은 현재 코드에서 유효한 JSON 예시다.

```json
{
  "memory_id": "mem_001",
  "session_id": "session_01",
  "turn_id": 42,
  "memory_type": "state",
  "content": "report_final.pdf",
  "token_count": 10,
  "created_at": "2026-08-15T14:00:00+09:00",
  "valid_from": "2026-08-15T14:00:00+09:00",
  "valid_to": null,
  "importance": 0.8,
  "is_active": true,
  "is_protected": false,
  "supersedes": "mem_000",
  "superseded_by": null,
  "memory_key": "report.current_file"
}
```

## 필드 계약

| 필드 | 계약 |
| --- | --- |
| `memory_id`, `session_id`, `content` | 비어 있지 않은 문자열 |
| `turn_id`, `token_count` | 0 이상의 정수이며 `bool`은 허용하지 않음 |
| `memory_type` | `constraint`, `state`, `preference`, `fact`, `experience` 중 하나 |
| `created_at`, `valid_from`, `valid_to` | `datetime` 또는 ISO 8601 문자열. `valid_to`만 `null` 가능 |
| `importance` | 0 이상 1 이하의 유한한 수, 기본값 `0.5` |
| `is_active` | 활성 버전 여부, 기본값 `true` |
| `is_protected` | 일반 Ranking보다 우선 보존할 제약 여부, 기본값 `false` |
| `supersedes`, `superseded_by` | 이전·다음 버전의 Memory ID 또는 `null` |
| `memory_key` | 같은 Session에서 변경되는 동일 정보를 묶는 Key 또는 `null`. `state`에는 필수 |

한 항목의 timestamp는 모두 timezone-aware이거나 모두 timezone-naive여야 한다. `valid_to`는 `valid_from`보다 빠를 수 없으며 유효 구간은 `[valid_from, valid_to)`이다. 활성 항목은 `superseded_by`를 가질 수 없고, 자기 자신을 대체하거나 두 대체 참조가 같은 ID를 가리킬 수 없다.

`to_dict()`는 Enum과 timestamp를 JSON 호환 문자열로 변환한다. `from_dict()`는 위 검증을 적용하며 알 수 없는 필드는 생성자 오류로 거부한다. Version Chain 연결, 동일 Session·Key의 ACTIVE 유일성, 참조 대상의 Session·Key 일치는 `memory/store.py`가 검증한다.

전체 Context Budget, 요청별 관련성, Recency와 Utility는 `MemoryItem` 필드가 아니다. `max_token`도 현재 schema에 존재하지 않으며 예산은 Selector와 Context Builder가 관리한다.
