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
      "retrieved_memory_ids": ["report_v1"],
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
- `retrieved_memory_ids`는 **General Retrieval 최초 Top-K 결과**를 순서대로
  전달한다. 별도 Protected Retrieval 및 Version Chain Expansion으로 추가된 ID는
  포함하지 않는다. `retrieved_memories`도 동일한 최초 Top-K의 실행 당시 snapshot이며
  Store에서 재조회하지 않는다. 위 예시는 최초 검색에서 `report_v1`을 찾은 뒤 버전
  확장으로 `report_final`을, 보호 검색으로 `approval_rule`을 최종 선택한 실행이다.
- `input_tokens`는 실제 최종 입력 측정값이다. tokenizer/API 사용량은 실행기가
  전달한다. 누락된 latency/cost/overflow는 null로 유지한다. 필수 기억 초과와
  formatting 초과를 구분할 수 없으므로 overflow를 status만으로 추정하지 않는다.

## 집계

TSR은 목표 달성, 기대 호출 충족, 금지 행동 없음, 제약 준수, 올바른 State,
승인 행동, 예산 준수를 모두 요구한다. 해당하지 않는 지표는 null이며 집계에서
제외한다. Recall@k, Precision@k, Hit@k, MRR의 정답 집합은
`target_memory_ids`에서 `applicable_constraint_ids`를 제외한 일반 검색 대상이다.
일반 검색 대상이 없으면 이 지표들은 null이다. precision@k 분모는 실제 반환된
고유 ID 수(최대 k)이고, MRR도 k 안에서 계산한다. 보호 제약의 최종 포함 여부는
`applicable_protected_recall`로 평가한다. CSA/CVR/SSUR 집계는 실행별 비율의
macro 평균이다. 검색 지표는 최초 General Retrieval Top-K만 평가하므로 최종 행동이 성공해도
Recall@k가 낮을 수 있다. 다음 두 결과를 분리한다.

- `general_retrieval_miss`: 일반 검색 대상 target(적용 제약 ID 제외)을 하나라도
  최초 Top-K에서 놓쳤으면 true이다. 별도 경로로 복구돼도 true를 유지하며 실패
  유형에 넣지 않는다. 일반 target이 없거나 검색 평가 대상이 아닌 전략은 null이다.
- `selection_failure`: 필수 `target_memory_ids` 중 하나라도 최종
  `selected_memory_ids`에 없으면 true이다. 보호 대상도 포함하며 최초 검색 결과와
  무관하다. target이 없으면 null이다. true이면 failure_types에 같은 이름을 기록한다.
  이는 최종 Context 누락을 뜻하며 선택 알고리즘이 원인이라는 의미는 아니다.

`general_retrieval_missing_target_ids`는 최초 검색에서 누락된 일반 target,
`recovered_target_ids`는 그중 최종 선택에서 복구된 target,
`missing_selected_target_ids`는 최종 Context에서 누락된 모든 필수 target이다.
`retrieval_failure`는 더 이상 failure_types에 기록하지 않는다. 두 bool 지표의
집계 평균은 각각 miss 비율과 최종 누락 비율이며 null은 분모에서 제외한다.
중간 후보 기록 없이 검색·버전 해석·예산 선택 중 실제 원인까지 확정하지 않는다.
작업 성공 여부는 실제 행동으로 별도 평가하므로 Context 누락과 독립적으로 기록한다.

## 찾았지만 예산 때문에 누락된 target 기록

기존 인터페이스의 `selection_result.rejected_memory_ids`는 시점 해석 이후 후보에서
제외된 ID이다. 이는 **후보로 찾았지만 선택하지 않았음**을 증명하지만 예산이 원인인지
보장하지 않는다. `context_result.dropped_flexible_ids`는 최종 토큰 검증에서 예산을
맞추려고 제거한 ID이므로 Context 단계의 예산 누락 근거로 사용한다.

실행 adapter는 원인과 후보 전체를 알고 있을 때 선택적으로 다음 trace를 제공한다.
evaluator가 다른 담당 모듈의 저장소를 조회하거나 Selector를 변경하지 않는다.

```json
{
  "selection_result": {
    "retrieved_memory_ids": ["report_v1"],
    "selected_memory_ids": ["approval_rule"],
    "rejected_memory_ids": ["report_final"]
  },
  "selection_trace": {
    "candidate_memory_ids": ["report_final", "approval_rule"],
    "budget_rejected_memory_ids": ["report_final"]
  },
  "context_result": {
    "selected_memory_ids": ["approval_rule"],
    "dropped_flexible_ids": []
  }
}
```

`candidate_memory_ids`는 시점/적용 여부 판별 후 **예산 선택 직전의 전체 후보**이며,
보호 후보도 포함한다. 오래된 최초 검색 ID를 이 목록에 섞지 않는다.
`budget_rejected_memory_ids`는 예산 때문에 선택되지 못한 ID의 전체 기록이다.
필수 기억 초과로 중단된 경우에도 실제로 발견한 후보 중 예산으로 누락된 ID만 기록한다.
원인을 모르면 필드를 생략하거나 null로 전달한다. 빈 배열은 해당 단계에 예산 누락이
없었다는 확정 기록이다. 이 trace는 평가 입력에 추가되는 선택적 기록이며 기존
`SelectionResult`의 필수 필드를 변경하지 않는다. 현재 Selector에는 명시적인 예산
원인 trace 생성이 없으므로 실행 adapter/담당 모듈 연결 시 제공해야 한다.

출력은 다음과 같다.

- `selection_candidate_memory_ids`: trace 및 선택/제외 기록으로 확인한 후보 ID.
  전체 후보 trace가 없으면 부분 목록이다. `selection_candidate_trace_complete`로 구분한다.
- `found_but_unselected_target_ids`: 발견 증거가 있지만 최종 Context에서 빠진 target.
- `selection_rejected_target_ids`: Selector의 제외 기록에 있는 최종 누락 target.
- `selection_budget_omitted_target_ids`: 명시적인 선택 단계 예산 기록에 있는 누락 target.
- `context_budget_omitted_target_ids`: formatting 이후 예산 조정으로 빠진 target.
- `budget_omitted_target_ids`: 위 두 예산 누락 target 목록의 합집합.
- `target_omission_reasons`: target별 `selection_budget`, `context_budget`,
  `selection_rejection_unspecified`, `unknown`을 기록한다.
- `budget_selection_failure`: 예산으로 target이 빠졌다는 근거가 있으면 true.
  최종 누락이 없거나 두 단계의 완전한 예산 기록으로 누락이 없음을 확인하면 false.
  target이 없거나 누락 원인을 확인할 기록이 부족하면 null.

확인된 예산 누락은 `selection_failure`와 함께 `budget_selection_failure` 실패 유형으로
기록하고 집계한다. 비율 집계에서 null은 제외하므로 근거 없는 누락을 false로 계산하지
않는다. `budget_omission_trace_complete`는 선택 및 Context 단계의 예산 기록을 모두
받았는지 나타낸다. `rejected_memory_ids`, `dropped_flexible_ids`와 formatting 전의
`pre_context_selected_memory_ids`도 출력에 보존한다.

일반 retrieval miss는 계속 최초 Top-K 기준으로만 계산한다. 위 예시는 최초 검색 누락,
버전 확장 후 발견, 선택 단계 예산 누락을 각각 따로 기록한다.

## 집계의 적용 범위

Sliding Window와 Long/Full Context 계열은 retrieval metric을 null로 둔다.
이 전략의 선택/시점 지표를 평가하려면 context에 포함된 정보의 Memory ID를
실행 adapter가 전달해야 한다.

95% CI는 sample_id 단위 cluster bootstrap으로 반복 실행의 의존성을 보존한다.
독립 sample이 하나뿐이면 CI는 null이다. RQ5는 동일 조건의 sample_id/repeat_id를
쌍으로 맞춘 뒤 Proposed − baseline 성공률 차이를 계산한다. 대응되지 않은 실행
수도 기록한다. 누락된 쌍을 실패로 대체하지 않는다. failure_types는 여러 원인을
동시에 기록하며, retrieval 실패를 stale 선택이나 행동 실패와 동일하게 취급하지
않는다. 설정의 grid는 입력 결과를 검증하지만 실행의 완전성을 보장하지 않는다.
