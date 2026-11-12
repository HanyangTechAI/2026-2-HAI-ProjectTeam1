# H100 고정 State Update 추가 실험

> 이 커밋은 보고서만 배포한다. 결과 JSON·JSONL·ZIP·그림과 실험 코드·설정은 별도로 보관하며, 아래 보관 경로와 재현 명령은 해당 로컬 산출물을 기준으로 한다. 핵심 수치와 해석은 본문 표에 포함했다.

사용자 요청에 따라 **H100·Update 5 및 H100·Update 10 두 셀만** 추가 실행한다. **실제 입력 80개 검증과 추가 640/640회 실행·분석을 완료했다.** 결과와 해석은 [분석 문서](../reports/state-update-h100-analysis-2026-11-11.md)를 따른다.

| 조건 | 신규 셀 ID | 과제 × 전략 × 반복 | 신규 실행 수 |
| --- | --- | --- | ---: |
| H100·Update 5 | `cell_4d4fa5737f9f` | 40 × 4 × 2 | 320 |
| H100·Update 10 | `cell_e16ea4569e72` | 40 × 4 × 2 | 320 |

공통 설정은 freeze 1.0.4, 입력 예산 2048 token, 원본 History 목표 128,000 token, seed 42/43, mode 1, Top-k 5, 제약 3, noise ratio 0.5다. 모델·프롬프트·출력 상한 2048·안전 여유 32와 최대 8 step은 기존 실험과 같다. 전략은 Proposed, Sliding Window, Vector Top-k, Utility-per-Token 네 가지다.

H100·Update 2는 기존 `experiments/results/long_horizon_v104/cell_69db7b3a7158/`의 결과를 참조하고 재실행하지 않는다. H1000·Update 5/10도 기존 결과를 참조한다. 추가 640회가 끝나면 기존 1,280회와 함께 H100/H1000 × Update 2/5/10의 6개 셀, 총 1,920개 고유 실행이 완성된다.

H100 고정의 Update 효과와, 동일한 Update 5 또는 10에서 H100↔H1000의 Horizon 효과를 각각 분석할 수 있다. 기존 연구의 정답 기억 패딩과 Top-k=5 조건을 유지하므로 그 영향을 없앤 별도 검증으로 해석하지 않는다.

## 입력 검증과 실행

실행 설정은 state_update_h100.json (별도 보관: `experiments/configs/state_update_h100.json`), 중복 방지 계획은 h100_extension_plan.json (별도 보관: `experiments/long_horizon/h100_extension_plan.json`), 실행기는 `experiments/long_horizon/append_h100.py`다.

입력 생성은 Update 10부터 시작하며 각 샘플을 체크포인트로 저장한다. 실측 History token ±1%, Memory token 재측정, 정확한 100-turn 거리, 6/11개 State 버전과 유효 기간·현재/과거 정답을 검증한다. 기존 H1000의 같은 Update 조건과 sample ID·query·Memory ID/content·정답도 일치해야 한다. 일부 샘플을 조용히 제외하거나 기존 결과를 덮어쓰지 않는다.

```sh
python -m experiments.long_horizon.append_h100 start
```

실행 중 같은 파이프라인을 다시 시작하지 않는다. 중단됐고 코드·설정·입력이 같을 때만 `start --resume`을 사용한다. 두 셀 완료 후 실행기가 고유 640개 키, Prompt audit, 비용 회계를 확인하고 집계한다. 별도 재집계는 `python -m experiments.long_horizon.append_h100 report`로 한다.

원본 입력은 `benchmark/generated/state_update_h100_v104/`, 실행 결과는 `experiments/results/state_update_h100_v104/`에 저장한다. 추가 결과는 [최종 분석](../reports/state-update-h100-analysis-2026-11-11.md)에 기록했다. 기존 분석 문서의 수치는 이 추가 실험 이전 결과로 유지한다.
