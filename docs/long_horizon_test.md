# 4. Long Horizon 및 State Update 확장 실험 설정

> 이 커밋은 보고서만 배포한다. 결과 JSON·JSONL·ZIP·그림과 실험 코드·설정은 별도로 보관하며, 아래 보관 경로와 재현 명령은 해당 로컬 산출물을 기준으로 한다. 핵심 수치와 해석은 본문 표에 포함했다.

상태: **실제 입력 160개 검증 및 1,280/1,280회 실행·분석 완료**. 최종 결과와 해석은 [분석 문서](long_horizon_analysis_20261111.md)를 따른다. Horizon과 State Update의 영향을 구분하는 두 실험으로 구성한다.

## 4-1. Long Horizon

같은 이력 token 양과 상태 변경 횟수에서 필요한 기억과 현재 과제의 turn 간격을 늘렸을 때의 변화를 확인한다.

| 항목 | 설정 |
| --- | --- |
| Horizon | **H100 / H1000** |
| State Update | **2회 고정** |
| 원본 History | **128,000 token 목표**, 두 Horizon 동일 |
| 모델 입력 Context Budget | **2048 token** |
| 비교 전략 | Full Proposed, Sliding Window, Vector Top-k, Utility-per-Token |
| 과제 / 반복 | 셀당 40개 기본 과제, 전략당 2회 반복 |
| 계획 실행 수 | 2셀 × 40과제 × 4전략 × 2반복 = **640회** |

실행 설정은 long_horizon.json (별도 보관: `experiments/configs/long_horizon.json`)이다. 기존 H100 결과는 History가 25K이므로 128K의 H1000과 직접 비교해 순수한 Horizon 효과라고 해석하지 않는다. **H100 대조 조건도 같은 128K 목표로 새로 생성·실행**한다. H100과 H1000은 같은 sample ID·query·Memory ID/content·정답을 공유해야 하며, turn 배치와 이력은 거리 조건에 맞게 달라진다.

H는 `current_turn − 가장 최근의 target memory turn`이다. 실제 Agent를 1,000번 행동시키거나 1,000단계 연속 과제를 수행하는 설정이 아니다. Agent의 최대 실행 단계는 기존처럼 8이다. H1000은 GPU 이름을 의미하지 않는다.

## 4-2. State Update Scaling — 별도 분석

Horizon을 고정하고 오래된 버전이 늘어날 때 올바른 현재·과거 상태를 고르는 능력을 비교한다.

| 항목 | 설정 |
| --- | --- |
| Horizon | **H1000 고정** |
| State Update | **2 / 5 / 10회** |
| 저장 State 버전 수 | 각각 **3 / 6 / 11개**: 초기값 + 변경 횟수 |
| 나머지 조건 | Long Horizon 실험과 동일 |
| 명목 실행 수 | 3셀 × 40과제 × 4전략 × 2반복 = 960회 |
| 추가 실행 수 | H1000·Update 2를 공유하면 **640회 추가** |

실행 설정은 state_update_scaling.json (별도 보관: `experiments/configs/state_update_scaling.json`)이다. 같은 기본 과제 식별자와 프로젝트·수신자를 유지하지만, 최신 파일과 정답은 업데이트 수에 따라 바뀐다. 예를 들어 현재 보고서는 Update 2에서 v3, Update 10에서 v11이다. 과거 조회는 요청 시점의 버전을 정답으로 유지한다.

구버전 후보 수의 증가는 이 실험의 조작이다. 무관 기록의 noise ratio는 0.5로 고정한다. 기존 정의는 `noise_tokens / (target_tokens + noise_tokens)`이며 구버전은 분모에 포함되지 않는다. 따라서 `structural_distractor_tokens`, 전체 Memory token과 버전 수도 별도 보고한다.

## 고유 실행 셀과 중복 방지

| 셀 ID | Horizon | Update | 사용처 | 실행 수 |
| --- | ---: | ---: | --- | ---: |
| `cell_69db7b3a7158` | 100 | 2 | Horizon 대조 | 320 |
| `cell_c0c1ff1d30dc` | 1000 | 2 | **두 실험 공통 기준** | 320 |
| `cell_bced8c7e24e9` | 1000 | 5 | Update 확장 | 320 |
| `cell_7f7a88a3b21b` | 1000 | 10 | Update 확장 | 320 |

고유 실행은 **4셀, 총 1,280회**다. 두 config를 별도로 생성해도 H1000·Update 2는 같은 셀 ID로 생성된다. 한 번만 실행하고 같은 원본 결과를 두 분석에서 참조한다. 중복 실행하거나 같은 결과를 독립 표본처럼 합산하지 않는다. 경로와 공유 관계는 plan.json (별도 보관: `experiments/long_horizon/plan.json`)에 있다.

## 통제 조건과 History 크기

- freeze 1.0.4, Gemini 3.1 Flash-Lite, temperature 0, thinking LOW, 출력 상한 2048 token, request safety margin 32 token.
- 임베딩·Top-k 5·효용 가중치·최대 8 step·공통 프롬프트·도구 정의를 유지한다. Update 10에서도 Top-k를 늘리지 않는다.
- seed 42/43, seed당 기본 과제 20개다. 셀당 현재 조회 10개·과거 조회 10개·승인 대기 10개·승인 완료 발송 10개, 합계 40개다.
- 제약 수 3, Noise 0.5, 생성 mode 1을 명시한다. 기존 Main과 같은 반복 문장 방식이며 자연어 다양성에 대한 실험은 아니다.
- Generator는 `history_turns = max(32, 2 × H + event 수 + 16)`을 사용한다. 오프라인 확인에서 H1000은 2,022~2,031개의 과거 turn을 만들었다. 기존 25K token 목표가 충분하다고 가정하지 않고 공통 목표를 128,000으로 잡았다.
- History 128K는 저장 이력의 크기이며 매 LLM 호출에 128K를 넣는다는 뜻이 아니다. 선택된 기억·프롬프트·feedback을 포함한 실제 입력은 기존 2K 상한을 유지한다.

128K는 아직 **Gemini CountTokens로 검증하지 않은 목표값**이다. 실제 생성에서 기본 형식 비용이 목표를 넘거나 허용 오차를 맞출 수 없으면 멈추고 모든 대조·확장 셀의 공통 History 목표를 함께 조정한다. H1000만 자동으로 늘리지 않는다.

## 실행 전 검증과 분석

4개 고유 셀 × 4개 과제 유형, 총 16개 조합을 공백 기준 `provisional_token_count`로 생성했다. 정확한 Horizon 거리, State 버전 3/6/11개, 활성 버전 한 개, 현재·과거 정답과 History 길이 목표 충족을 확인했다. 이는 생성 구조 검증이며 실제 Gemini token 측정이나 LLM 성능 측정이 아니다.

실제 생성 후에는 History token 수가 목표 128,000의 ±1%인지, Memory content가 실행 모델과 같은 counter로 측정됐는지, version chain과 유효 기간이 일관적인지, Horizon 두 조건의 query·Memory content·정답이 pair되는지 확인한다. 샘플을 조용히 제외하거나 버전 chain을 잘라 생성하지 않는다.

주요 지표는 작업 성공률, Required State 정확도, stale-state 사용률, 승인 제약 위반률이다. 현재·과거 조회와 승인 대기·승인 완료를 분리한다. Target이 Top-5에 없었던 검색 실패, 후보가 있지만 잘못된 버전을 고른 선택 실패, 정답을 보존했으나 attachment 같은 행동 인자가 빠진 실패를 구분한다. Required/Protected 예산 초과와 실제 prompt 초과도 분리한다.

Horizon 효과는 **Update 2에서 H100↔H1000**, Update 효과는 **H1000에서 2↔5↔10**만 비교한다. 기본 과제 40개를 군집으로 두 반복을 함께 bootstrap한다(2,000회, seed 42). 총 실행 수를 독립 과제 수로 해석하지 않는다.

## 실제 생성·실행 경로

키는 기존 방식으로 프로세스 환경의 `GEMINI_API_KEY`에 둔다. 초기 설정 검토에서는 API를 호출하지 않았으며, 실행 승인 후 실제 CountTokens 생성 파이프라인을 시작했다.

```sh
python -m benchmark.generator --config experiments/configs/long_horizon.json --gemini-token-model gemini-3.1-flash-lite --gemini-token-cache .cache/long_horizon_generation.sqlite3 --output benchmark/generated/long_horizon_v104
python -m benchmark.generator --config experiments/configs/state_update_scaling.json --gemini-token-model gemini-3.1-flash-lite --gemini-token-cache .cache/long_horizon_generation.sqlite3 --output benchmark/generated/state_update_scaling_v104
```

검증을 통과하면 plan의 고유 4셀만 Runner로 실행한다. 예를 들어 두 실험의 공통 기준 셀은 다음과 같다.

```sh
python -m benchmark.runner --config experiments/configs/long_horizon.json --scenarios benchmark/generated/long_horizon_v104/cell_c0c1ff1d30dc.json --environment-agent-factory benchmark.experiment_runtime:environment_agent --output experiments/results/long_horizon_v104/cell_c0c1ff1d30dc --checkpoint-interval 10
```

Update 확장은 plan에 지정된 Update 5·10의 두 셀을 추가 실행한다. 공유 셀은 같은 출력 경로를 참조한다. 기존 checkpoint의 설정이나 입력을 바꾼 뒤 resume하지 않는다.


## 실행 파이프라인

`experiments/long_horizon/run.py`가 가장 큰 H1000·Update 10부터 입력을 생성한다. 샘플별 파일과 생성 코드·설정 해시를 저장해 중단 후 동일 설정으로 이어갈 수 있다. 모든 입력의 실제 token·버전 체인·Horizon을 확인하고 H100/H1000의 query·Memory content pairing까지 통과해야 Agent 실행을 시작한다.

현재 생성물과 사전 검증 기록은 `benchmark/generated/long_horizon_suite_v104/`, 개별 셀 입력·결과는 plan에 지정된 경로에 저장한다. 검증 완료 후 셀을 최대 2개씩 병렬 실행하고 각 셀 완료 후 다음 셀로 넘어간다. 전체 완료 시 고유 1,280개 실행 키를 감사하고 `experiments/results/long_horizon_suite_v104/`에 집계한다. 병렬 실행의 지연시간은 참고 지표로 해석한다.

```sh
python -m experiments.long_horizon.run start --workers 2
```

실행 중 같은 파이프라인을 다시 시작하지 않는다. 중단됐고 입력·설정·코드가 동일할 때만 `start --resume`을 사용한다. 실행기를 포함한 전체 unittest 362개가 통과했다(1개 skip). 실행이 완료됐으며 최종 집계와 진단은 [분석 문서](long_horizon_analysis_20261111.md)에 기록했다.
