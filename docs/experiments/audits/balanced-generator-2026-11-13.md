# 균형 시나리오 생성기 변경 및 검증 (2026-11-13)

기본 생성 설계를 `balanced_v2`로 변경했다. 시나리오 유형을 층화 배분하고,
제약이 실제 파일 후보 또는 승인 행동을 제한하도록 구성했다. 기존 설계는
명시적 `legacy_v1`로 재현할 수 있으며 기존 입력·실험 결과는 덮어쓰지 않았다.

## 변경 내용

- 현재 조회·과거 조회·승인 대기·승인 완료를 순환 배정한다. seed 경계에서도
  순환을 이어서, 한 셀의 네 유형 개수 차이가 최대 1개가 되도록 한다.
  task를 하나만 지정하면 해당 task의 두 하위 유형을 같은 방식으로 배분한다.
- 조회 State에 C+1개, 이메일 State에 C개의 버전별 파일 후보를 둔다(C는 저장 제약 수).
  조회 제약 C개 또는 이메일의 파일 제약 C−1개가 실제 후보를 각각 제한한다.
  나머지 이메일 제약 1개는 승인 의무를 표현한다. 적용 제약은 모두 평가 GT에 들어간다.
- 승인 완료 번호를 승인 제약에 포함하고 별도 fact의 중복 승인은 제거한다.
- 정답 후보 번호를 순환시키고 표시 순서를 섞는다. 첫 번째/마지막 후보 또는 특정
  번호만 골라서 전체 유형을 해결하는 고정 단서를 피한다. 첫 후보가 허용되는 사례도
  포함하므로 “항상 첫 후보를 건너뛴다”는 방식도 전체를 해결하지 못한다.
- 규칙 Planner는 보이는 State와 후보 제약을 해석하도록 확장했다. LLM 프롬프트,
  Proposed selector의 우선 보존 제거 의미, evaluator 성공 기준은 유지했다.
- profile을 sample/cell ID와 metadata·manifest에 기록하고 실제 coverage를 셀마다
  저장한다. 기본 출력 경로는 `_balanced_v2`로 분리한다.
- 표준 config의 freeze는 `1.0.5`다. 표준 Runner는 이전·누락·혼합 profile 입력을
  API client 생성 전에 거부한다. Config 없는 별도 legacy 실행은 유지한다.

각 파일 제약은 실제 후보를 금지하므로 하나를 잃으면 합법적인 행동 집합이 달라진다.
이것이 개별 제약 하나를 제거할 때마다 특정 모델의 선택이 반드시 바뀐다는 뜻은 아니다.
생성기는 시험 기회를 균형화하며, ablation별 성공률 하락폭을 같게 만들지는 않는다.
특히 packing 효과는 실제 tokenizer와 실효 예산에서의 경쟁 정도에 달려 있다.

## 생성 결과

Main config를 네트워크 없이 provisional tokenizer로 생성해 12개 셀을 확인했다.
셀별 독립 task identity는 같은 40개이며 480개 출현을 독립 표본으로 합산하지 않는다.

| 셀당 항목 | 기존 Ablation 입력 | balanced_v2 |
|---|---:|---:|
| 현재 조회 / 과거 조회 / 승인 대기 / 승인 완료 | 각각 10개 | 각각 10개 |
| 제약 적용 시나리오 | 20/40 (50%) | 40/40 (100%) |
| 저장 제약 중 적용 제약 | 20/120 (16.7%) | 120/120 (100%) |
| 실제 State 후보를 제한하는 파일 규칙 | 0개 | 100개 |
| 승인 행동 규칙 | 20개 | 20개 |
| Required State가 있는 시나리오 | 40/40 | 40/40 |
| 버전 선택이 필요한 시나리오 | 40/40 | 40/40 |
| 별도 중복 승인 fact | 10개 | 0개 |

새 데이터의 파일 규칙은 후보 번호를 사용하므로 constraint 본문에 PDF 파일명을
직접 적지 않는다. Audit의 기존 `filename_prohibition_count`는 본문의 리터럴 파일명
개수를 세는 값이므로 새 데이터에서는 0이다. 실제 후보 연결은 별도
`rules_restricting_visible_candidates_count=100`으로 검증한다.

## 검증과 한계

- 생성기·후보 Planner·selector·evaluator·Runner·context 관련 테스트 260개 실행,
  259개 통과 및 1개 skip, 실패 없음.
- 12개 셀의 정답 tool trace 총 480개가 실제 evaluator를 통과했다.
- 금지된 실제 후보를 선택하면 해당 constraint violation이 검출된다.
- 실제 protected 검색의 결과와 선언된 적용 제약이 일치함을 테스트했다.
- 2K/H100의 40개는 실제 다단계 Runner/selector/context/규칙 Planner/mock tool
  경로에서 40/40 성공했다. 이 통합 점검은 constant test embedding, top-k 20과
  provisional tokenizer를 사용하므로 LLM 성능이나 실험 top-k 5 검색 성능 결과가 아니다.
- 홀수/작은 표본과 task subset, C=0 또는 state update=0, Budget/Horizon/filler
  대응 관계 및 legacy 콘텐츠 해시를 검증했다.
- 별도 runtime metadata 테스트 모듈은 로컬 `huggingface_hub` 의존성 부재로 import할
  수 없었다. 위 260개 실행에는 포함하지 않았다.

새 Gemini CountTokens·임베딩 모델·LLM API 호출은 수행하지 않았다. 실제 모델 비교에는
같은 profile로 Gemini token 기준 입력을 새로 생성한 후 새 결과 경로에서 실행해야 한다.
기존 어블레이션 결과는 새 생성기로 수정된 결과가 아니므로 함께 합산하지 않는다.

## 재현 및 산출물

```powershell
python -m benchmark.generator --config experiments/configs/main.json --archive
python -m benchmark.constraint_coverage_audit --dataset benchmark/generated/main_balanced_v2.zip --output-dir docs/results/constraint_coverage_balanced_v2_20261113
```

- 생성 코드: [`generator.py`](../../../benchmark/generator.py)
- 사용법 및 모델 token 측정 명령: [생성기 가이드](../../guides/generator-usage.md)
- 셀별 coverage·해시·통합 점검 조건: [validation.json](../../results/constraint_coverage_balanced_v2_20261113/validation.json)
- 적용 제약 집계: [summary.json](../../results/constraint_coverage_balanced_v2_20261113/summary.json)
- 시나리오별 제약 연결: [details.csv](../../results/constraint_coverage_balanced_v2_20261113/details.csv)
- 로컬 신규 데이터: `benchmark/generated/main_balanced_v2.zip` (generated 경로로 Git 제외)
