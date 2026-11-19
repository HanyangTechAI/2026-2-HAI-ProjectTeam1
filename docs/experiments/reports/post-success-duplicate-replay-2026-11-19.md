# 성공 후 Side-Effect 중복 재생 평가 — 2026-11-19

## 요약

성공한 이메일 발송 action을 같은 pipeline run 안에서 그대로 한 번 더 제안하는 상황을 만들고, 실행별 idempotency를 켠 arm과 끈 arm을 paired 비교했다. 전체 Holdout은 80개 후보 중 최초 Gemini 제안이 정답인 79개를 비교에 포함했다. idempotency ON은 승인 이메일 40쌍의 두 번째 발송을 모두 Tool 실행 전에 차단했다. OFF에서는 40쌍 모두 두 번째 발송이 실행됐다. 읽기 전용 파일 조회 대조군에서는 ON의 오차단이 39쌍 중 0건이었다.

전체 158회에서 두 arm은 각각 goal을 79/79 완료했다. 모든 run은 status ok였고 budget stop은 없었다. 2,048-token 예산의 실제 prompt 최댓값은 1,345 tokens였다. 별도 smoke 8개에서도 이메일 중복 4/4 차단, 파일 조회 오차단 0/4, goal 완료 8/8을 관측했으며 Main에 합산하지 않았다.

이는 결정적으로 재생한 동일 action과 mock Tool에서 run-local ledger 동작을 확인한 결과다. 자연 발생 중복 빈도, 네트워크 재시도, 실제 외부 발송 또는 외부 서비스의 exactly-once 보장은 평가하지 않았다.

## 실험 설정

| 항목 | 설정 |
| --- | --- |
| 실험 | post_success_duplicate_v1 |
| 데이터 | Freeze 1.0.6 기반, Gemini CountTokens로 생성한 독립 synthetic Holdout |
| 모델 | gemini-3.1-flash-lite, temperature 0, thinking LOW |
| 입력 예산 및 실행 한도 | 2,048 tokens, 최대 8 steps, action rejection 최대 2회 |
| 비교 arm | idempotency_on / idempotency_off |
| 최초 제안 | 실제 Gemini 제안을 arm 간 공유 |
| 반복 주입 | 두 번째 proposal에 첫 proposal과 동일한 action을 결정적으로 주입 |
| 이후 계획 | live Gemini planner |
| Side-effect 범위 | 한 pipeline run |
| 읽기 전용 대조군 | file.find_file |

Main 입력은 seed 2100–2119의 160개 전체 시나리오에서 긍정 과제 80개를 골랐다. S2 및 S3_VALID의 approved_email 40개와 file_lookup 40개로 구성된다. 최초 proposal이 정답이 아니었던 file_lookup 한 건(tcs235_file_lookup_2101_s3_valid)은 두 arm 비교에서 제외됐다. 따라서 분석 분모는 이메일 40쌍, 파일 조회 39쌍이며 158회 실행이다.

Smoke는 seed 2000–2001의 별도 입력에서 긍정 과제 8개를 사용했다. approved_email 4개와 file_lookup 4개 모두 최초 제안이 정답이어서 16회 paired 실행을 기록했다.

## 결과

### 전체 Holdout

| 과제군 | paired 과제 수 | ON 중복 차단 | ON 두 번째 Tool 실행 | OFF 두 번째 Tool 실행 | Goal 완료 |
| --- | ---: | ---: | ---: | ---: | ---: |
| approved_email | 40 | 40/40 | 0 | 40/40 | ON 40/40, OFF 40/40 |
| file_lookup 대조군 | 39 | 해당 없음 | 39/39 | 39/39 | ON 39/39, OFF 39/39 |

중복 예방률은 40/40(100%, Wilson 95% CI 91.2–100%)이었다. ON의 성공 이메일 발송은 40건이고 초과 발송은 0건이었다. OFF에서는 성공 발송 80건 중 40건이 초과 발송이었다. 파일 조회 대조군 오차단은 0/39(0%, Wilson 95% CI 0–9.0%)였다.

ON/OFF 각각 79회 모두 pipeline status가 ok였고 goal 완료 79/79, budget stop 0건이었다. Main prompt audit 434건 중 실제 prompt token 수가 있는 276건은 모두 2,048-token 예산 이내였고, 최댓값은 1,345 tokens였다. 나머지 158건은 결정적 중복 주입(step 2)으로 Gemini API 호출이 없어 실제 prompt token 수가 없다. 첫 제안 공유 호출과 이후 live 호출을 합해 Gemini API 198회, 추정 비용 USD 0.12171을 기록했다(cost_complete=true).

### 별도 smoke

| 항목 | 관측 결과 |
| --- | --- |
| 후보 및 paired 실행 | 8개, 총 16회 |
| 이메일 중복 차단 | 4/4; OFF에서는 4건 초과 발송 |
| 파일 조회 오차단 | 0/4 |
| Goal 완료 및 budget stop | 두 arm 모두 8/8 완료, budget stop 0건 |
| Prompt 및 사용량 | prompt audit 44건 중 28건에 실제 token 수가 있음(최대 1,309); 16건은 결정적 주입으로 API 미호출; API 20회 |
| 추정 비용 | USD 0.01276925 (cost_complete=true) |

Smoke의 표본은 작아 Main 추정에 합치지 않는다. 그 표본에서의 Wilson 95% CI는 중복 차단 4/4가 51.0–100%, 읽기 전용 오차단 0/4가 0–49.0%다.

## 해석과 제한

- 첫 LLM proposal은 두 arm이 공유했고, 반복 action은 의도적으로 동일하게 주입했다. 자연 상태에서 중복 제안이 얼마나 자주 생기는지는 측정하지 않았다.
- 40개의 이메일 paired 결과에서 ledger ON은 같은 run의 두 번째 send_email 호출을 막았다. 이 실험은 다른 side-effect action, 서로 다른 인자 재시도, 네트워크 장애 뒤 외부 서비스의 응답 불확실성을 평가하지 않았다.
- 파일 조회는 read-only negative control이다. 오차단 0/39는 이 표본에서의 관측이며 일반적인 오차단률 추정으로 확대하지 않는다.
- email과 file Tool은 mock 구현이다. 외부 메일 발송이나 실제 파일 작업은 일어나지 않았다.
- 최초 proposal이 틀린 파일 조회 1건은 Main paired 비교에서 제외됐다. 결과를 80개 전체 후보의 성공률로 표현하지 않는다.
- 결과는 synthetic positive Holdout과 고정 2,048-token 설정에 한정된다. 일반적인 idempotency 보장이나 기본 Proposed 경로의 동작을 입증하지 않는다.

## 재현 자료

- Main 입력: [duplicate_holdout_2100](../../../benchmark/generated/duplicate_holdout_2100/) — 전체 160 scenarios, 분석 후보 80개. Dataset SHA-256: **430e30ebe4c5f4d0ecff9b827b8f08e4b4ebe2cf89a52d466628880cbfbd7404**; labels SHA-256: **aa482b5a8f35aea92b6259393a908b01de2fda7c2e87beda023fe03917bf4536**.
- Main 결과: [post_success_duplicate_full_v1](../../../experiments/results/post_success_duplicate_full_v1/) — manifest, 최초 proposal, raw runs, summary.
- Smoke 입력: [duplicate_smoke_2000](../../../benchmark/generated/duplicate_smoke_2000/) — 전체 16 scenarios, 분석 후보 8개. Dataset SHA-256: **ecae29af27de0ad8e2f30449b0473fceacfe71656fbeeef8a2897b37afad9126**; labels SHA-256: **dc93f57bd37cf853462a6b8ca7e8b93d6e39cab2787a3bb86ade8938e375aa0c**.
- Smoke 결과: [post_success_duplicate_smoke_v1](../../../experiments/results/post_success_duplicate_smoke_v1/).
- Config: [ablation.json](../../../experiments/configs/ablation.json), SHA-256 **3d5a48cd434238f4ff61506f5a994014d85902886e1d410aeaa396ff1b298800**.
- 실험 source: [post_success_duplicate.py](../../../benchmark/post_success_duplicate.py), SHA-256 **34c57865cc8963eedc88e1b1ff6b7d963d0e043c441281b84e5979a2f679b522**. Freeze 기반 commit은 **7cd9600d99f171dcd4ff1c4a1afd44f1322fbffa**이며 manifest는 실험 당시 작업 트리가 dirty였음을 기록한다. 추가 source hash와 시스템·Tool hash는 각 결과 manifest를 따른다.

기록된 실행과 동등한 명령:

    python -m benchmark.post_success_duplicate --dataset benchmark/generated/duplicate_holdout_2100 --config experiments/configs/ablation.json --output experiments/results/post_success_duplicate_full_v1 --fixed-budget 2048
    python -m benchmark.post_success_duplicate --dataset benchmark/generated/duplicate_smoke_2000 --config experiments/configs/ablation.json --output experiments/results/post_success_duplicate_smoke_v1 --fixed-budget 2048 --max-cases 8

재실행은 새로운 Gemini 응답을 생성하므로 기존 산출물을 보존하려면 별도 output 경로를 지정한다. 이 문서 갱신에서는 코드나 실험 결과 JSON을 수정하지 않았고 테스트도 실행하지 않았다.
