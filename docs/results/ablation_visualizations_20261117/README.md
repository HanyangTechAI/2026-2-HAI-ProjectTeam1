# Ablation SVG 시각화

**생성일 2026-11-17 · 실험일 2026-11-11 · SVG 7개**

[전체 미리보기](index.html) · [SVG 및 재생성 코드 ZIP](ablation_svg_20261117.zip)

현재 저장소와 GitHub에서 확인되는 최신 구성요소 제거 성능 결과를 따로 정리했습니다. 11월 17일의 topic_balanced_v3 생성 점검 및 Safety Gate 오류 복구는 이 ablation과 다른 실험입니다.

## 결과와 해석

- 2K와 4K의 다섯 조건은 각각 40개 기본 과제 × 2회 반복입니다. 두 예산의 400개 대응 실행은 같은 결과였으며 독립 과제 수가 늘어난 것은 아닙니다.
- Full 성공률은 97.5%, 제약 보존 제거는 100%, 시간 해석과 필수 상태 동시 제거는 90%, 나머지 두 조건은 97.5%입니다. 짝지은 차이의 CI는 모두 0에 닿거나 포함합니다.
- 제약 보존 제거의 성공률 100%와 보호 기억 회수율 90%를 함께 봐야 합니다. 기능 제거가 전반적으로 더 낫다는 결론은 지지하지 않습니다.
- v1.0.3의 No temporal은 시간 해석과 필수 상태 보존을 함께 끕니다. v1.0.4의 Temporal 단독 제거 재실행은 7번 그림으로 구분했습니다.
- 2K의 JSON 집계를 직접 읽고, 4K와 추가 재실행의 수치는 보고서 표에서 가져왔습니다. 없는 원본 로그나 신뢰구간을 만들지 않았습니다.

## 그림별 안내

| 파일 | 설명 |
|---|---|
| [ablation_01_task_success.svg](svg/ablation_01_task_success.svg) | **작업 성공률** — 2K와 4K에서 Full과 네 제거 조건을 비교합니다. 두 예산의 400개 대응 실행은 같은 결과였으며, v1.0.3의 No temporal은 필수 상태 보존도 함께 제거했습니다. |
| [ablation_02_memory_and_behavior.svg](svg/ablation_02_memory_and_behavior.svg) | **기억 보존과 행동 신뢰성** — 보호 기억 회수, 시간 문맥 정확성, 실제 상태 사용, 오래된 상태 사용을 나눠 보여줍니다. 성공률이 높아도 보호 기억 보존이 낮아질 수 있습니다. |
| [ablation_03_paired_success_difference.svg](svg/ablation_03_paired_success_difference.svg) | **Full 대비 성공률 차이** — 같은 과제와 반복을 맞춘 Full − 제거 조건의 차이입니다. 저장된 짝지은 신뢰구간이 모두 0에 닿거나 포함하므로 확정적 우위나 동등성으로 해석하지 않습니다. |
| [ablation_04_token_usage.svg](svg/ablation_04_token_usage.svg) | **입력과 출력 토큰** — 왼쪽 입력은 실행별 최대 단일 Context의 평균이고, 오른쪽 출력은 실행 전체 누적 출력의 평균입니다. 입력 곡선을 여러 호출의 누적 입력량으로 읽으면 안 됩니다. |
| [ablation_05_cost_and_latency.svg](svg/ablation_05_cost_and_latency.svg) | **추정 비용과 지연 시간** — 실행당 생성 비용과 두 실행 환경에서의 지연 시간을 비교합니다. 2K와 4K의 OS·commit이 달라 지연 차이를 예산 효과로 해석하지 않습니다. |
| [ablation_06_retrieval_and_failures.svg](svg/ablation_06_retrieval_and_failures.svg) | **검색 성능과 실패 진단** — 초기 검색 점수와 선택·상태·제약 실패를 함께 비교합니다. 실패 지표는 서로 겹칠 수 있으며, 적용 분모는 CSV에 기록했습니다. |
| [ablation_07_temporal_only_rerun.svg](svg/ablation_07_temporal_only_rerun.svg) | **Temporal 단독 제거 재실행** — 필수 상태 보존을 복구한 v1.0.4의 Temporal 단독 제거 결과를 기존 조건과 구분합니다. 수정 전후 80쌍의 성공 판정과 최종 선택 기억 집합은 같았지만, 일반적인 기능 불필요성을 입증한 결과는 아닙니다. |

## 파일 구성과 재생성

`svg/`는 글자와 도형을 편집할 수 있는 SVG, `previews/`는 PNG 미리보기, `data/`는 그림에 사용한 수치 CSV입니다. 출처와 SHA-256은 `manifest.json`에 있습니다.

저장소 루트에서 다음 명령으로 다시 생성합니다.

```powershell
.\.venv\Scripts\python.exe experiments/plot_ablation_results.py
```

ZIP의 `reproduce/`에는 코드·사용한 집계·보고서가 같은 상대 경로로 들어 있습니다. 필요한 패키지는 `reproduce/experiments/visualization/requirements.txt`에 명시되어 있습니다.
