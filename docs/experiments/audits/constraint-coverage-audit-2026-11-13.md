# Constraint 시나리오 적용 비율 및 Ablation 민감도 점검 (2026-11-13)

이 문서는 `balanced_v2` 도입 전의 `legacy_v1` 생성기와 기존 실험 입력을 점검한 기록이다.
이후 적용한 수정과 새 입력 검증은 [균형 생성기 변경 보고서](balanced-generator-2026-11-13.md)를 따른다.

현재 ablation 설정의 생성기는 Constraint Preservation의 효과를 관측하기에 민감도가 낮은 구조다.
시나리오 절반에만 평가상 적용 제약이 있고, 저장된 제약의 83.3%는 적용 대상에서 제외된다.
또한 기존 `no_constraint_preservation`은 제약 내용 삭제가 아니라 우선 보존 기능 제거다.
따라서 기존의 성공률 100%를 “constraints를 제거해도 성공했다”로 해석할 수 없다.
이는 고정 생성 설계와 실제 제약 노출량을 확인한 결론이며, 각 요인의 성공률에 대한
인과적 기여를 분리한 새 LLM 실험 결과는 아니다.

## 입력과 집계 단위

- 기존 실행: [2K/H100 Ablation 집계](../../results/ablation_2k_h100_20261111/summary.json).
- 입력 검증: [validation.json](../../results/ablation_2k_h100_20261111/validation.json)의
  `dataset_cell_sha256`와 로컬 보존 ZIP의 셀 바이트가 일치함을 확인했다.
- 입력 ZIP: `benchmark/generated/main_gemini_3_1_flash_lite.zip`.
- 셀: `cell_e5534bba1746.json`, Budget 2,048 / Horizon 100 / History 25K /
  Noise 0.5 / Constraint 3 / State update 2.
- 셀 SHA-256: `d26d015af2a8ab060a95b027f0e142eacaecd9c33af4dcc7ef5585d1b254dcec`.
- 독립 시나리오는 seed 42/43의 총 40개다. 전략별 80 runs는 이 40개를 두 번 반복한 값이다.
- 같은 ZIP의 12개 셀에서도 적용 비율은 동일하다. 480개 시나리오 출현은 동일한
  40개 task identity를 Budget/Horizon별로 재사용한 것이므로 독립 480개로 세지 않는다.
- 점검 당시 checkout의 `generate_dataset`도 ablation config의 2K 단일 셀을 오프라인
  provisional tokenizer로 생성해 확인했다. 시나리오 40개, 저장 제약 120개, 적용 제약
  20개, 금지 파일과 정답 파일 충돌 0개로 동일했다. 이 재생성은 구성 확인이며 원본
  Gemini token count나 실행 프롬프트를 재현한 것은 아니다.

## 실제로 제약을 건드리는 비율

여기서 “적용”은 `applicable_constraint_ids`와 `constraint_rules`에 지정된 것을 뜻한다.
기억에 제약이 존재하는 것, 평가에 적용되는 것, 제약 문장이 없으면 행동이 바뀌는 것은
서로 다른 측정이다.

| 유형 | 독립 시나리오 | 전체 비율 | 저장 제약 | 평가 적용 제약 |
|---|---:|---:|---:|---:|
| 현재 보고서 조회 | 10 | 25% | 30 | 0 |
| 과거 보고서 조회 | 10 | 25% | 30 | 0 |
| 승인 대기 이메일 | 10 | 25% | 30 | 10 |
| 승인 완료 이메일 | 10 | 25% | 30 | 10 |
| 합계 | 40 | 100% | 120 | 20 |

- 제약 기억을 보유한 시나리오: **40/40 = 100%**.
- 평가상 제약이 적용되는 시나리오: **20/40 = 50%**.
- 저장된 제약 중 평가에 적용되는 비율: **20/120 = 16.7%**.
- 적용 대상에서 제외된 제약: **100/120 = 83.3%**.
- 승인 대기 시나리오: **10/40 = 25%**. 승인 완료 사실이 따로 없는 상태에서
  승인 정책의 보존 효과를 살피기 좋은 하위집합이다. 이것을 실측한 “제약 필수 비율”로
  부르지는 않는다.

생성기의 [`generator.py`](../../../benchmark/generator.py) 348–366행은 제약 세 개를
만들지만, 387–392행은 이메일에서 첫 승인 제약 하나만 적용하고 조회에서는 전부 제외한다.
나머지 100개는 `restricted_*.pdf`의 조회/첨부를 금지하며 실제 정답인
`*_report_v*.pdf`와 충돌하지 않는다. 따라서 이 표본에는 금지 파일과 정답 파일이
충돌하여 선택을 바꿔야 하는 사례가 없다.

이 배분은 난수 추출의 우연이 아니다. 651–664행에서 task/subtype을 고정 분할한다.
현재 두 task의 50:50 구성에서는 `constraint_count=C`를 늘려도 적용 제약은
시나리오당 0개 또는 1개다. 따라서 전체 저장 제약 중 적용 비율은 `1/(2*C)`로 줄어든다.
Constraint 수를 늘리는 실험이 실제 적용 제약 수 증가를 의미하지 않는다는 한계가 있다.

### 보존 데이터 간 차이

저장소에 tracked된 `benchmark/generated/main.zip`은 실제 ablation 입력과 다르다.
그 파일은 조회 과제의 파일 금지 제약까지 포함해 **40/40개 시나리오, 120/120개 제약을
적용 대상으로 선언**한다. 하지만 그 안에서도 파일 금지 100개와 정답 파일의 충돌은
0건이다. 선언상 적용 비율이 높다는 사실만으로 행동을 바꾸는 제약이 많다고 볼 수 없다.

| 입력 | 제약 적용 시나리오 | 적용 제약 / 저장 제약 | 금지 파일과 정답 파일 충돌 |
|---|---:|---:|---:|
| 실제 Ablation 입력과 SHA 일치하는 Gemini ZIP | 20/40 | 20/120 | 0/100 |
| tracked `main.zip` | 40/40 | 120/120 | 0/100 |

이는 tokenization뿐 아니라 ground-truth 적용성 metadata도 다르다는 뜻이다.
이번 ablation 해석에는 validation과 SHA가 일치하는 첫 번째 입력을 사용한다.
두 번째 입력의 집계와 provenance도 `tracked_main_summary.json`에 별도로 남겼다.

## 제약 보존 제거 조건에서 실제로 사라진 양

[`selector.py`](../../../memory/selector.py) 602행은 우선 보존을 끄면 제약을 일반
검색 후보에 포함한다. 668–681행은 전용 protected 경로만 생략하고, 748행은 그 제약이
flexible 선택에 남을 수 있게 한다. [`test_ablation.py`](../../../tests/unit/memory/test_ablation.py)
749–758행도 이 동작을 명시적으로 검증한다. 이는 현재 ablation의 의도된 의미다.

검색으로 살아남은 제약은 [`builder.py`](../../../context/builder.py) 61–63행에서 다시
`[PROTECTED CONSTRAINTS]`로 표시된다. 표시 위치가 바뀌어도 flexible로 선택된 기억의
예산 탈락 가능성은 유지된다.

| 2K/H100 조건 | 성공 | 적용 제약 회수 | 적용 제약 누락 |
|---|---:|---:|---:|
| Full Proposed | 78/80 = 97.5% | 40/40 = 100% | 0/40 |
| No constraint preservation | 80/80 = 100% | 36/40 = 90% | 4/40 |

입력 셀에는 적용 제약이 시나리오당 하나이므로 recall 90%는 적용 대상 실행 40회 중
36회에서 제약이 남고 4회에서 빠졌다는 뜻이다. 전체 80회 중 실제 적용 제약 누락에
노출된 것은 **4/80 = 5%**다. 나머지 76회는 적용 제약이 원래 없거나 계속 보유했다.
반복된 4회를 독립 시나리오 4개라고 해석하지 않는다. 로컬에 개별 원본 run 로그가
없어, 누락된 시나리오의 identity/subtype은 이번 점검에서 재확인하지 않았다.

추가로 기존 validation의 실제 입력 최대치는 **1,823 tokens**로 실효 예산 2,016보다
낮다. 기존 보고서의 1,822와 차이가 있으므로 여기서는 validation 값을 사용했다.
기존 [실험 보고서](../reports/ablation-2k-h100-analysis-2026-11-11.md)는 2K/4K의 선택과
행동 결과가 동일했다고 기록한다. 이 표본에서 예산 확대가 차이를 만들지 않았다는
관측이며, 모든 예산에서 제약 보존이 불필요하다는 근거는 아니다.

## 중복 단서와 평가 확인

승인 완료 10개는 별도 fact에 “외부 발송 승인”과 승인 번호가 있다
(`generator.py` 399–406행). 승인 정책 제약이 없어져도 발송 근거와 번호는 다른 기억에
남는다. 공통 [`llm_planner.py`](../../../agent/llm_planner.py) 79–94행도 접수번호와
발송 허가를 구별하고 접수번호로 발송하지 말라고 지시한다. 이는 모델의 승인 처리에
도움을 줄 수 있으나, 프로젝트별 승인 의무를 직접 대체한다고 단정하지 않는다.

평가기 [`evaluator.py`](../../../benchmark/evaluator.py) 245–260행은 실제 무승인 발송을
위반으로 검사한다. 올바른 호출 이후 추가한 무승인 발송도 검사되는지 인위적인
tool trace로 점검했다. 이 점검은 LLM을 호출하지 않은 평가기 진단이다.

- 올바른 expected call은 선택 기억에서 제약을 전부 빼도 40/40 성공한다.
  행동 준수를 평가하고 recall은 따로 보고하는 현재 지표 정의에 맞는다.
- 잘못된 무승인 send 하나만 실행하면 승인 과제 20/20에서 실패한다.
- 승인 완료 10개에서 올바른 발송 뒤에 무승인 발송을 추가하면 10/10 실패한다.
  평가 규칙만 인위적으로 제거하면 이 추가 발송을 잡지 못해 10/10 성공으로 바뀐다.

따라서 실제 적용 승인 규칙을 평가기가 무시해서 100%가 된다는 증거는 없다.
Expected call과 top-level forbidden call에도 승인 행동 기준이 중복되지만, 이는
평가 기준의 중복이며 LLM에 정답이 노출됐다는 증거가 아니다.

## 후속 실험에 필요한 변경

1. 기존 `no_constraint_preservation`은 **우선 보존 제거**로 유지하고 해석한다.
   별도로 **제약 내용 삭제 진단 조건**을 두어 모든 검색·최근 이력 경로를 포함한
   최종 Context에서 해당 제약이 실제로 없는지 검증한다. 평가 GT는 그대로 유지한다.
2. 전체 TSR과 함께 승인 대기/승인 완료/제약 비적용 TSR, 최종 적용 제약 노출률,
   제약 누락 실행에서의 행동 준수율을 보고한다. 현재는 적용 제약 누락 4회가
   어느 subtype인지 원본 로그를 확보해 확인하는 것이 우선이다.
3. 동일 query·state·사실에 승인 의무/면제 정책만 바꾼 대응 시나리오를 만든다.
   금지 파일/수신자/시간 등의 제약도 정답 행동을 실제로 바꾸는 사례로 구성한다.
   적용 제약 수와 무관한 방해 제약 수를 별도 변수로 관리한다.
4. 공통 승인 지침은 모든 조건에서 고정하고, 정책 면제 사례도 포함해 항상 승인
   요청만 하는 기본 행동으로 높은 점수를 얻는지 검사한다. Full/no-preservation의
   제약 노출 차이가 생기는 유효 예산 범위를 먼저 확인한 뒤 LLM 반복 실험을 한다.

이번 작업은 기존 generator·selector·실험 설정·기존 결과를 수정하지 않았다.
새 LLM/임베딩/CountTokens API 호출도 수행하지 않았다.

## 산출물과 재현

재현 코드는 [`constraint_coverage_audit.py`](../../../benchmark/constraint_coverage_audit.py),
집계와 시나리오별 자료는 [결과 폴더](../../results/constraint_coverage_20261113/)에 보존한다.

```powershell
.venv/Scripts/python.exe -m benchmark.constraint_coverage_audit --dataset benchmark/generated/main_gemini_3_1_flash_lite.zip --validation-file docs/results/ablation_2k_h100_20261111/validation.json --budget 2048 --horizon 100 --output-dir docs/results/constraint_coverage_20261113
```

tracked `benchmark/generated/main.zip`을 조사할 때는 별도 `--output-dir`을 사용하고
`--validation-file`을 전달하지 않는다. 위 표처럼 적용성 metadata와 셀 SHA가 달라
실제 ablation 입력을 대체할 수 없다.

기존 ablation·evaluator·generator·generator-runner 관련 단위 테스트 **108개가 통과**했다.
