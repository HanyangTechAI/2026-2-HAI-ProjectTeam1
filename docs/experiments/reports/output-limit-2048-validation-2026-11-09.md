# 출력 상한 2048 / freeze 1.0.3 검증 결과

실행일: 2026-11-09 (Asia/Seoul).

## 변경 내용

공통 출력 상한을 512에서 2048 token으로 변경하고 freeze를 1.0.3으로 올렸다. 모델·temperature 0·thinking LOW·프롬프트·입력 Budget·검색/선택·평가 기준은 유지했다. 모든 config와 Planner/Runner 기본값 및 freeze 검증이 같은 상한을 사용한다. 출력 상한은 통제변인이며 실제 출력 token은 측정값이다.

응답별 finish_reasons와 candidate/thoughts token을 run_result.llm_usage.response_diagnostics에 기록하도록 했다. 이는 진단 추가이며 응답 파싱·성공 판정·재시도 정책을 변경하지 않는다.

## 실행 범위

앞선 512 상한 점검과 동일한 입력 조건 10개를 전략별 한 번씩 실행했다. 전체 40회이며 정규 본실험 12셀 전체를 실행한 결과가 아니다. 반복 수만 main 설정의 2회에서 1회로 override하고 새 결과 경로에 저장했다. 입력은 기존 main_gemini_3_1_flash_lite.zip에서 가져왔다.

Budget별 입력 수는 1K 2개, 2K 4개, 4K 2개, 8K 2개다. Horizon 10/50/100 및 현재 조회 3개·과거 조회 3개·승인 전 요청 2개·승인된 발송 2개를 포함한다. 각 전략은 같은 입력과 가상 Tool 초기 환경을 사용했다. Seed 42/43은 각각 5개다.

## 전략별 결과

| 전략 | 이전 512 작업 성공 | 새 2048 작업 성공 | 새 예산 사전 중단 | 이전 응답 해석 오류 | 새 응답 해석 오류 |
|---|---:|---:|---:|---:|---:|
| Proposed | 8/10 | 8/10 | 2 | 0 | 0 |
| Sliding Window | 6/10 | 6/10 | 0 | 1 | 0 |
| Vector Top-k | 4/10 | 6/10 | 0 | 3 | 0 |
| Utility-per-Token | 6/10 | 6/10 | 0 | 1 | 0 |

| Budget | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
|---|---:|---:|---:|---:|
| 1024 | 0/2 | 0/2 | 0/2 | 0/2 |
| 2048 | 4/4 | 2/4 | 3/4 | 3/4 |
| 4096 | 2/2 | 2/2 | 2/2 | 2/2 |
| 8192 | 2/2 | 2/2 | 1/2 | 1/2 |

## 실행과 기록 검증

- 40/40 실행 키와 completed checkpoint를 중복·누락 없이 확인했다. 실제 LLM 실행은 38회이며, Proposed의 1K 두 조건은 Mandatory Memory 예산 부족으로 모델 호출 전에 중단했다.
- 생성 API 75회, 성공 응답 75회. finish_reason 집계는 {"STOP": 75}다.
- 실제 LLM 실행 38/38회에서 prompt audit가 verified다. 실제 입력 예산 초과 0건, CountTokens 실효 예산 초과 0건, 최대 양의 측정 차이 1 token이다.
- 적용 제약 16회 평가에서 제약 위반 0건이다. 잘못된 파일 버전 사용은 승인 절차 위반과 별도 평가한다.
- 비용 회계 complete 40/40, 생성 비용 추정 합계 $0.063740. 고정 단가와 관측 usage 기반 추정이며 실제 청구서 금액은 아니다.
- 모든 배치의 실행 freeze=1.0.3, max_output_tokens=2048, safety margin=32를 확인했다. 모델·출력 상한 CLI override는 없고, override는 반복 수와 저장 경로뿐이다.
- 임베딩 resolved revision은 `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`이며 prompt checksum은 모든 배치에서 일치한다.
- 전체 unittest는 322개 중 321개 통과, 실제 API smoke 1개 skip이다. 실제 API 검증은 별도 40회 실행으로 수행했다.

## 해석

출력 512에서 잘렸던 5개 조건의 별도 예비 재현에서는 MAX_TOKENS 5건을 확인했고, 상한만 2048로 바꾼 예비 점검에서는 5/5 작업 성공·10개 응답 모두 STOP이었다. 이번에는 2048을 정식 공통 설정으로 적용해 전체 10개 조건을 전략별 재실행했다.

출력 오류와 별개로 1K의 필수 입력 예산 부족, Baseline의 정답 기억 누락·이전 파일 버전 사용은 성능 결과로 남긴다. 응답 해석 오류와 MAX_TOKENS는 독립 진단으로 함께 보고하며, 기존 Action-Level TSR의 정의를 사후 변경하지 않는다.

선택한 입력 조건 수가 작고 Budget/Horizon/Task를 완전 교차한 설계가 아니므로 전략 우월성의 통계적 확정이나 정규 본실험 전체의 안정성을 주장하지 않는다. 출력 상한 변경 전후 결과도 같은 결과군으로 합치지 않는다.

## 재현과 보존

- 기존 512 실행 결과와 checkpoint는 보존했다. 기존 실행을 resume하거나 덮어쓰지 않았다.
- 입력 ZIP은 모델·입력·content counter가 같으므로 원래 생성 freeze 1.0.1 및 검증 보고서를 보존해 재사용했다. 실행 freeze와 생성 freeze를 구분한다.
- 실행 기반 Git HEAD는 `9135efc0c156fcc9919b36c1475852264ba6f954`이고 tracked 변경사항이 있으므로 git_dirty=true다. 실제 코드 내용 식별자는 각 experiment.json의 resume_identity.code에 보존된다.
- 로컬 원본 결과 경로: `experiments/results/final_check_2048_freeze103_20261109/`.
- selection.json은 입력 선정·ZIP 해시·명령, analysis.json은 집계·무결성 확인, run_details.json과 runs.csv는 실행별 행동·진단, 각 셀 폴더는 원본 runs.jsonl·experiment.json·progress.json·summary.json을 보존한다.
- 실험 원본 결과 디렉터리는 저장소의 기존 .gitignore 정책을 따른다. 이 문서는 요약을 저장소 문서로 보존한다.
