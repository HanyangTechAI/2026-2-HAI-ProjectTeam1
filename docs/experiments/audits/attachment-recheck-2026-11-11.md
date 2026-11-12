# Horizon Narrative Approval Attachment Recheck (2026-11-11)

## 1. 목적과 결론

[Mode 1/2 Gemini Agent 실행](narrative-llm-execution-audit-2026-11-11.md)에서 Proposed가
실패한 승인 과제 `seed43_approval_014_pending`을 고정해 Mode 1과 Mode 2에서 각각 5회
반복했다. Mode 1은 5회 모두 정답 파일을 `email.request_approval`의 `attachment` 인자에
넣지 않아 실패했고, Mode 2는 5회 모두 올바른 첨부를 전달해 성공했다.

다섯 Mode 1 요청은 이메일 본문에 파일명을 언급했지만 구조화된 Tool 인자에는 첨부를
넣지 않았다. Target 검색 hit와 선택은 5/5였고 최종 Context의 temporal state도 5/5
정확했다. 따라서 이 반복에서 확인된 문제는 lookup 누락이 아니라, Context의 Required
State를 실제 Tool 인자로 옮기는 행동 단계에 있었다. Mode 2에서는 같은 query와 정답
파일로 올바른 Tool 인자가 반복 관측됐다. 단일 과제 진단이므로 일반적인 Mode 효과의
추정이나 인과 증명은 아니다.

## 2. 설계와 설정

| 항목 | 값 |
|---|---|
| Scenario | `seed43_approval_014_pending` 한 건 |
| 반복 수 | Mode별 Proposed 5회, 총 10회 |
| Mode 1 | 기존 반복 Horizon filler (`horizon_generation_mode=1`, `repeated`) |
| Mode 2 | 로컬 자연어 Horizon filler (`horizon_generation_mode=2`, `random_natural`) |
| 정답 파일 | `31d65bfb18_report_v3.pdf` |
| 기대 Tool call | `email.request_approval`에 올바른 `attachment` 포함 |
| 모델 | Gemini 3.1 Flash-Lite, temperature 0, thinking LOW |
| Freeze | Main v1.0.3, top-k 5, 최대 8 steps |
| Budget | 명목 2,048 / Safety Margin 32 / 실효 상한 2,016 tokens |
| 셀 | History 25K / Horizon 100 / Noise 0.5 |
| Constraint | 승인 전 발송 금지; 이번 과제는 승인 요청만 수행 |

두 Mode는 같은 Scenario ID, query, query context, Required State·target ID, 승인 조건,
기대 Tool call과 token-count mode를 사용했다. 둘 다 commit `6ad1c3b`의 같은 prompt와
runtime에서 실행했다. Horizon Memory 서술은 Mode에 따라 달랐다.

## 3. 행동 결과

| 입력 Mode | Task Success | Target 검색 hit | 선택 Target recall | Context temporal state | Required State 정확도 | 정답 attachment |
|---|---:|---:|---:|---:|---:|---:|
| 1 (`repeated`) | 0/5 | 5/5 | 5/5 | 5/5 | 0/5 | 0/5 |
| 2 (`random_natural`) | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |

Mode 1의 다섯 실행 모두 `request_approval` 자체는 성공 접수됐지만 `attachment`가
`null`로 처리됐다. 이메일 본문에는 파일명이 있었어도 평가기는 Tool 인자에 요구된 파일
State가 반영되지 않은 것을 `state_omission`과 `temporal_failure`로 기록했다. Mode 2의
다섯 요청은 정답 파일을 첨부했다. 두 Mode 모두 `approval_id=approval_001`은 `pending`
접수번호로만 생성됐고, 승인 완료나 발송으로 취급되지 않았다. `send_email`은 호출되지
않았다.

이 분리는 핵심이다. Mode 1에서는 retrieve miss와 selected-target 누락이 없고 최종
Context의 temporal 상태도 정확했지만, Agent가 파일명을 본문에만 언급하고 필수 Tool
인자에 넣지 않았다. 따라서 Mode 1에서 `temporal_context_correct`는 5/5지만 행동을
평가하는 `temporal_version_correct`는 0/5다. 이 재점검 범위에서는 검색·선택보다 행동
인자 구성이 실패 지점이었다.

## 4. 예산·비용 및 실행 무결성

| Mode | 평균 입력 tokens | 평균 출력 tokens | 평균 planning latency | 평균 기록 비용 |
|---|---:|---:|---:|---:|
| 1 | 1,540 | 864 | 5,090 ms | $0.0020230 |
| 2 | 1,564 | 898 | 4,973 ms | $0.0020815 |

양쪽 모두 5/5에서 Planner API 응답 2회가 성공했고 usage 및 비용 산정이 완전했다. Prompt
Audit는 10/10 `verified`였다. 각 실행에서 request-aware CountTokens와 실제 prompt
측정의 차이는 두 요청 모두 `+1 token`이었고, 최대 실제 prompt는 Mode 1 1,541 tokens,
Mode 2 1,565 tokens로 실효 상한 2,016보다 낮았다. Mandatory Memory overflow, Budget
초과, Constraint Violation, final-parse fallback 및 미승인 발송은 모두 0회다.

기록 비용은 provider usage 기반 추정치이며 청구서 합계로 보지 않는다. 두 조건의 결과
파일에서 각 5개 run의 비용 산정 완전성을 확인했다.

## 5. 한계와 해석

- 서로 다른 기본 과제가 아니라 단일 승인 과제를 Mode당 5회 반복했다. 다섯 반복을 독립
  과제 다섯 개로 간주하거나 TSR 모집단 추정에 사용하지 않는다.
- Mode 1/2 사이에는 Horizon Memory의 문장 표현이 바뀐다. 이 결과만으로 표현 방식이
  attachment 누락의 원인이라고 확정하지 않으며, Mode 일반 효과의 검정도 아니다.
- temperature 0에서 각 Mode의 다섯 출력은 일관됐지만, 다른 시점·모델·프롬프트와 더 많은
  승인 과제에서 같은 패턴이 반복될지는 확인하지 않았다.
- 실제 이메일은 발송되지 않았다. 승인 요청과 Tool 인자 전달만 mock Tool 환경에서 평가했다.
- 이 결과를 Main 12셀, Mode 1/2의 40개 성능 비교, 또는 기존 실패 집계의 추가 독립 표본으로
  합산하지 않는다.

## 6. 재현 정보와 산출물

- 입력 Mode 1: `benchmark/generated/narrative_mode1/cell_e5534bba1746.json`
- 입력 Mode 2: `benchmark/generated/narrative_mode2/cell_3ea70c1fbe6b.json`
- Mode 1 실행: `experiments/results/attachment_recheck_mode1/`
- Mode 2 실행: `experiments/results/attachment_recheck_mode2/`
- 각 결과의 `experiment.json`, `progress.json`, `runs.jsonl`, `summary.json`
- 실행 안내: [generator-usage.md](../../guides/generator-usage.md#승인-과제-attachment-반복-재점검)

