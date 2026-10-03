# 실험 평가 실행 방법

`benchmark/evaluator.py`는 Python 표준 라이브러리만 사용한다. Memory Store나
LLM을 호출하지 않고 시나리오 정답과 실행 snapshot을 평가한다. 다른 담당자의
Agent/Scenario dataclass도 받을 수 있지만 아래 추가 평가 필드는 adapter에서
제공해야 한다. 다른 모듈의 공통 자료형을 새로 정의하거나 변경하지 않는다.

## 설정

`experiments/configs/`의 JSON 파일을 사용한다.

| 파일 | 목적 |
| --- | --- |
| `smoke.json` | 작은 통합 확인용 설정 |
| `main.json` | RQ1/3/4/5, History × Budget × Horizon |
| `stress.json` | RQ2, 제약 수 × 상태 변경 × Noise |
| `ablation.json` | Proposed 구성 요소 제거 비교 |
| `reference.json` | 큰 예산의 Full Context 참고 실험, 제한 예산 순위와 분리 |

`model.provider`, `model.name`은 아직 선정되지 않아 `null`이다. 실제 실행 전
모델, tokenizer, system prompt, tool 정의와 baseline 구현을 동일 조건으로
고정해야 한다. 이 설정은 실행 계획이며 Agent 실행기나 데이터 생성기는 아니다.
`ablation.json`의 `strategy_options`는 향후 실행기가 전달할 구성 요소 옵션이다.
Budget Optimization 제거는 flexible 기억을 retrieval 순서로 넣되 전체 예산
상한은 그대로 지키는 조건이다. 현재 evaluator는 선택 알고리즘을 실행하지 않는다.
`dataset.samples_per_cell`은 각 seed/조건의 전체 sample 수이며 task 유형들에
나누어 배정한다. seed는 데이터 생성용, repeat_id는 모델 반복 실행용이다.
서로 다른 seed로 생성한 sample은 sample_id도 달라야 한다.
`reference.json`의 128K 예산은 모델 선정 후 context window와 출력 예약량을
확인하여 조정해야 하는 초기 설정이다.

`expand_config()`는 조건 × strategy × seed × repeat 행을 생성한다. sample
생성은 포함하지 않는다. 가능하지 않은 History/Horizon 조합은 데이터 생성기에서
실험 전에 검증해야 한다. 1K보다 고정 입력이 크면 해당 조건을 사전에 조정하고
사유를 기록한다. 예산 초과 Full Context는 별도 설정/결과로 평가한다.

## 실행

레포 루트에서:

```powershell
python -m benchmark.evaluator --config experiments/configs/smoke.json --plan-only --output experiments/results/smoke
python -m benchmark.evaluator --config experiments/configs/smoke.json --input experiments/results/runs.jsonl --output experiments/results/smoke
python -m unittest discover -s tests -p "test_evaluator.py"
```

입력 JSONL은 각 줄마다 `{ "scenario": {...}, "run_result": {...},
"strategy": "proposed", "repeat_id": 0 }` 형식이다. 결과는
`evaluations.jsonl`, `summary.json`, `deltas.json`, `config.json`에 저장한다.
출력 경로의 같은 이름 파일은 덮어쓴다. `--output`이 실제 출력 경로이며 설정의
`output_directory`는 권장 경로이다.

## 최소 입력 예시

다음 객체를 한 줄 JSON으로 직렬화하면 smoke 설정에서 평가할 수 있다.

```json
{
  "strategy": "proposed",
  "repeat_id": 0,
  "scenario": {
    "scenario_id": "seed42_report_001",
    "context_budget": 2048,
    "target_memory_ids": ["report_final", "approval_rule"],
    "required_state_ids": ["report_final"],
    "applicable_constraint_ids": ["approval_rule"],
    "temporal_version_required": true,
    "wrong_version_memory_ids": ["report_v1"],
    "required_states": [{
      "memory_id": "report_final",
      "tool_call": {"tool_name": "email", "action": "request_approval"},
      "argument": "attachment",
      "expected_value": "report_final.pdf",
      "stale_values": ["report_v1.pdf"]
    }],
    "constraint_rules": [{
      "constraint_id": "approval_rule",
      "approval": {
        "execution": {"tool_name": "email", "action": "send_email"},
        "request": {"tool_name": "email", "action": "request_approval"},
        "request_required": true,
        "valid_approval_ids": []
      }
    }],
    "expected_tool_calls": [{"tool_name": "email", "action": "request_approval"}],
    "forbidden_tool_calls": [],
    "metadata": {
      "history_length_tokens": 10000,
      "horizon_turns": 50,
      "noise_memory_ratio": 0.5,
      "constraint_count": 1,
      "state_update_count": 2
    }
  },
  "run_result": {
    "status": "ok",
    "goal_completed": true,
    "selection_result": {
      "retrieved_memory_ids": ["report_final", "approval_rule"],
      "selected_memory_ids": ["report_final", "approval_rule"],
      "memory_tokens": 80
    },
    "tool_calls": [{
      "tool_name": "email",
      "action": "request_approval",
      "arguments": {"attachment": "report_final.pdf"}
    }],
    "usage": {"fixed_input_tokens": 310, "input_tokens": 390, "output_tokens": 18},
    "planning_latency_ms": 250,
    "total_latency_ms": 1200,
    "total_llm_cost": null
  }
}
```

## 판정 계약

- `goal_completed`는 시나리오 목표를 실제로 달성했는지 실행기/Tool 결과에서
  확정한 bool이다. 호출을 시도한 것만으로 true로 만들면 안 된다. 승인 요청이
  목표인 시나리오는 승인 요청 성공을 완료로 본다. 단순 응답 문제는
  `expected_answers: ["정답", "허용하는 다른 정답"]`로 정규화 EM을 사용할 수 있다.
  응답 정답과 goal_completed를 모두 제공한 경우 둘 다 만족해야 성공이다.
- `required_states`와 `constraint_rules`는 각각 ID 목록과 정확하게 대응해야 한다.
  자연어 제약이나 Memory ID만으로 행동 정답을 추정하지 않는다.
- Tool pattern은 객체 부분 일치이고 값은 정확하게 비교한다. 기대 호출은 순서와
  무관하게 각각 다른 호출과 매칭한다. 승인 ID는 실행 시 이미 승인된 ID만
  `valid_approval_ids`에 넣는다. 요청만 했다고 승인된 것으로 취급하지 않는다.
  승인 획득까지 포함하는 다단계 실험은 단계별로 snapshot을 만들어 평가한다.
- State는 관련 Tool 호출의 argument에서 평가한다. 여러 호출에서 한 번이라도
  잘못 사용하면 그 항목은 실패이다. stale 값과 임의 값이 모두 사용되면 stale과
  error 둘 다 기록되므로 두 비율은 상호 배타적이지 않다.
- 과거 질문에서는 `expected_value`와 `required_state_ids`를 해당 시점의 정답으로
  지정한다. `wrong_version_memory_ids`는 질문 시점에 틀린 버전만 넣는다.
  단순한 비활성 여부로 stale 오류를 판정하지 않는다.
- `context_result.selected_memory_ids`가 있으면 formatting 후 실제 context를
  기준으로 평가한다. 없으면 `selection_result.selected_memory_ids`를 사용한다.
  예산 오류로 중단된 실행은 최종 선택을 빈 집합으로 평가한다.
- `retrieved_memory_ids`는 비교 대상 검색 경로의 union을 adapter에서 전달한다.
  일반 검색/보호 검색/버전 확장 중 어느 범위를 포함하는지 모든 전략에서 고정한다.
  `retrieved_memories`는 실행 당시 snapshot이며 Store에서 재조회하지 않는다.
- `input_tokens`는 실제 최종 입력 측정값이다. tokenizer/API 사용량은 실행기가
  전달한다. 누락된 latency/cost/overflow는 null로 유지한다. 필수 기억 초과와
  formatting 초과를 구분할 수 없으므로 overflow를 status만으로 추정하지 않는다.

## 집계

TSR은 목표 달성, 기대 호출 충족, 금지 행동 없음, 제약 준수, 올바른 State,
승인 행동, 예산 준수를 모두 요구한다. 해당하지 않는 지표는 null이며 집계에서
제외한다. precision@k 분모는 실제 반환된 고유 ID 수(최대 k)이다. MRR도 k 안에서
계산한다. CSA/CVR/SSUR 집계는 실행별 비율의 macro 평균이다.
Sliding Window와 Long/Full Context 계열은 retrieval metric을 null로 둔다.
이 전략의 선택/시점 지표를 평가하려면 context에 포함된 정보의 Memory ID를
실행 adapter가 전달해야 한다.

95% CI는 sample_id 단위 cluster bootstrap으로 반복 실행의 의존성을 보존한다.
독립 sample이 하나뿐이면 CI는 null이다. RQ5는 동일 조건의 sample_id/repeat_id를
쌍으로 맞춘 뒤 Proposed − baseline 성공률 차이를 계산한다. 대응되지 않은 실행
수도 기록한다. 누락된 쌍을 실패로 대체하지 않는다. failure_types는 여러 원인을
동시에 기록하며, retrieval 실패를 stale 선택이나 행동 실패와 동일하게 취급하지
않는다. 설정의 grid는 입력 결과를 검증하지만 실행의 완전성을 보장하지 않는다.
