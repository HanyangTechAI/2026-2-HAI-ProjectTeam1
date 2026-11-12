# Pipeline Smoke 점검 — 2026-11-12

## 목적과 범위

고정 smoke 입력 5개를 네 전략으로 각각 한 번 실행한 파이프라인 점검이다. 같은 입력을 11월 12일 세 차례 실행했으며, 아래 표는 가장 나중 실행을 기준으로 한다. 반복 실행은 같은 과제의 재실행이므로 60개의 독립 표본으로 합산하지 않는다. 전략 간 성능을 추정하기 위한 실험이 아니며, Main·Ablation·Critical Flexible 결과에 합산하지 않는다.

| 항목 | 설정 |
|---|---|
| 최신 실행 commit | `0f098a89b6dc7a775c5dbed6853b70c93cb7d0bc` (clean) |
| 입력 | seed 42의 temporal update 3개, approval 2개 |
| 전략 | Proposed, Sliding Window, Vector Top-k, Utility-per-Token |
| 규모 | 실행당 5 scenarios × 4 strategies × 1 repeat = 20 runs; 같은 입력으로 총 3회 실행 |
| Agent | Gemini `gemini-3.1-flash-lite`, temperature 0, output limit 2048, max steps 8 |

## 관측 결과

| 전략 | 완료 run | Task success | Evaluator failure counts |
|---|---:|---:|---|
| Proposed | 5/5 | 5/5 | 없음 |
| Sliding Window | 5/5 | 4/5 | selection failure 2, budget selection failure 2, temporal failure 1, state omission 1 |
| Vector Top-k | 5/5 | 5/5 | 없음 |
| Utility-per-Token | 5/5 | 5/5 | 없음 |

20회 모두 Pipeline status `ok`, 종료 사유 `completed`였다. 실제 입력 Budget 초과는 0회였고, 제약 적용 대상 2개 scenario를 전략별로 평가한 8건에서 Constraint violation은 0건이었다. Expected Tool call은 19/20회에서 관측됐다. Sliding Window의 실패 1건은 `seed42_temporal_update_002_historical`에서 발생했다. failure diagnostics는 서로 겹칠 수 있으므로 표의 개수를 run 수처럼 합산하지 않는다.

같은 20-run smoke를 17:04, 18:24, 21:09에 총 세 차례 실행했다. 세 run 모두 네 전략의 Task success가 각각 Proposed 5/5, Sliding Window 4/5, Vector Top-k 5/5, Utility-per-Token 5/5였고, Expected Tool call 19/20, budget overflow 0, Constraint violation 0/8 applicable evaluations로 같았다. Planning latency는 실행마다 달라지므로 비교 지표로 해석하거나 세 run을 합산하지 않는다.

## 해석 한계와 산출물

각 scenario를 한 번씩만 실행한 작은 fixed smoke set이다. 관측된 5/5 또는 4/5 성공률로 전략 우위나 일반화 성능을 주장하지 않는다. 이 실행은 Agent·Tool·Evaluator 연결이 끝까지 동작하는지와 한 개 historical 조회 경로의 실패 진단을 확인하는 용도다.

최신 원본 결과는 `experiments/results/smoke_20261112_210924_186268/`에 있다. 앞선 동일 입력 재실행은 `experiments/results/smoke_20261112_170437_901140/` 및 `experiments/results/smoke_20261112_182422_020919/`에 보존돼 있다. 각 `experiment.json`은 commit·모델·prompt checksum과 scenario ID를, `progress.json`과 `summary.json`은 진행·집계를, `runs.jsonl`은 scenario와 개별 실행 결과를 보존한다. 실행 metadata에는 원본 scenario 파일 경로가 없으므로 정확한 입력 재생성에는 `runs.jsonl`의 scenario snapshot을 사용해야 한다. 결과 디렉터리는 `.gitignore` 정책을 따른다.

21:40 재시도 `experiments/results/smoke_20261112_214002_157359/`는 `interrupted`, 0/20 completed이며 LLM 호출과 run 결과가 없다. 이 중단 실행은 위의 완료된 세 차례 비교에 포함하지 않았다. 저장된 metadata에는 중단 원인이 기록돼 있지 않다.
