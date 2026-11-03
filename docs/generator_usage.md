# 실험 시나리오 생성기 사용법

`benchmark/generator.py`는 실험 설정의 데이터 축에 맞춰 `temporal_update`와 `approval` 입력 시나리오를 생성한다. 생성물에는 연속된 turn의 합성 대화 이력, 버전 관계를 보존한 Memory snapshot, 행동 평가용 정답과 실측 메타데이터가 들어간다. Agent 실행 결과나 모델 성능 측정치는 들어가지 않는다.

기존 [`benchmark/scenarios.json`](../benchmark/scenarios.json)의 S01~S39는 모듈 경계와 예외 정책을 검사하는 최소 fixture로 유지한다. 긴 이력을 사용하는 실험 데이터와 그 fixture의 용도는 다르다. 기존 fixture 설명은 [scenarios_usage.md](scenarios_usage.md)를 참고한다.

## 생성 명령

저장소 루트에서 다음 명령을 실행한다.

```powershell
python -m benchmark.generator --config experiments/configs/smoke.json --output benchmark/generated/smoke
```

기본 Token Counter는 공백으로 나눈 단어 수를 세는 `provisional_token_count`이다. 네트워크와 API key 없이 재현 가능한 개발용 데이터를 만들 수 있다. 이 수치를 실제 모델의 token 사용량으로 보고하지 않는다.

실제 모델의 token 기준으로 데이터를 생성하려면 모든 비교 전략에 사용할 것과 같은 counter를 지정한다. 다음 두 옵션은 동시에 사용할 수 없다.

```powershell
python -m benchmark.generator --config experiments/configs/smoke.json --output benchmark/generated/smoke_custom --token-counter my_package.tokenizers:count_tokens

python -m benchmark.generator --config experiments/configs/smoke.json --output benchmark/generated/smoke_gemini --gemini-token-model MODEL --gemini-token-cache-size 4096
```

`--token-counter`는 문자열 하나를 받아 음수가 아닌 정수 token 수를 반환하는 `module:function` 형식의 함수를 받는다. Gemini 측정에는 설치된 `google-genai`와 `GEMINI_API_KEY`가 필요하며 CountTokens API를 호출한다. 실제 사용할 모델명을 `MODEL` 자리에 입력한다. Counter를 바꾸면 History와 Memory 비용도 다시 측정되므로 기존 생성물의 metadata만 바꾸어 재사용하지 않는다.

필수 실험 데이터는 설정 경로와 출력 경로를 바꾸어 생성한다.

```powershell
python -m benchmark.generator --config experiments/configs/main.json --archive --output benchmark/generated/main.zip
Expand-Archive -LiteralPath benchmark/generated/main.zip -DestinationPath benchmark/generated/main
```

`--archive`는 셀 JSON과 루트의 `manifest.json`을 ZIP 한 개에 저장한다. Main의 반복되는 긴 대화를 압축해 보관하는 방식이며 데이터와 정답은 디렉터리 출력과 같다. Runner는 ZIP을 직접 읽지 않으므로 압축을 푼 뒤 셀 JSON 하나를 지정한다. 압축 없이 만들려면 `--archive`를 생략하고 출력 디렉터리를 지정한다.

Python 코드에서는 같은 기능을 다음과 같이 호출할 수 있다.

```python
from benchmark.evaluator import load_config
from benchmark.generator import generate_dataset, render_history, write_dataset
from context.tokenizer import provisional_token_count

config = load_config("experiments/configs/smoke.json")
payload = generate_dataset(config, token_counter=provisional_token_count)
write_dataset(payload, "benchmark/generated/smoke")
```

`generate_scenario(...)`는 단일 시나리오를 생성하는 keyword 기반 API이다. 직접 호출할 때도 `history_length_tokens=10000`처럼 충분한 이력 길이를 지정한다. `render_history(history)`는 생성기가 History 길이를 측정하는 동일한 문자열 표현을 반환하므로, JSON 직렬화 길이나 Memory token 합계를 History token 수로 대신 사용하지 않는다.

## 출력과 sample 배정

### Pilot 전달용 독립 sample 100개

`experiments/configs/pilot.json`은 단일 seed·단일 셀에서 서로 다른 기본 sample 100개를 생성한다. 현재 보고서 조회 25개, 과거 보고서 조회 25개, 승인 요청 25개, 이미 승인된 발송 25개로 구성한다. Budget/Horizon을 바꾸어 같은 과제를 반복 배치한 개수가 아니며, `sample_id`, `scenario_id`, `session_id`가 각각 100개 모두 고유하다. seed는 기존 main/smoke의 42·43과 겹치지 않도록 2026을 사용한다.

설정은 Budget 2048, History 25000, Horizon 50 turns, Noise 0.5, 저장된 Constraint 3개, State update 2회다. 현재 생성기의 적용 제약은 `temporal_update`에서 0개, `approval`에서 발송 승인 제약 1개다. 저장 수는 `metadata.constraint_count`, 적용 수는 `metadata.applicable_constraint_count`와 `applicable_constraint_ids`로 구분한다. 옵션을 생략한 생성 명령은 공백 기준 개발용 counter를 사용한다. 이 전달물은 기존 템플릿에 서로 다른 과제 식별자·파일·세션을 부여한 합성 입력이며 실제 Agent 실행 결과는 포함하지 않는다.

```powershell
python -m benchmark.generator --config experiments/configs/pilot.json --output benchmark/generated/pilot
python -m benchmark.generator --config experiments/configs/pilot.json --archive --output benchmark/generated/pilot.zip
```

압축 없는 JSON은 `benchmark/generated/pilot/manifest.json`의 `cells[0].path`에 있다. `benchmark/generated/pilot.zip`에도 같은 manifest와 셀 JSON이 들어 있어 전달용으로 사용할 수 있다. 기존 Scenario와 같은 최상위 `scenarios` 배열을 유지하므로 JSON을 읽는 기존 코드에 전달할 수 있다.

개수는 `dataset.samples_per_cell`, seed는 `seeds`에서 바꾼다. `grid.horizon_turns`, `noise_memory_ratio`, `state_update_count`, `constraint_count`도 조절할 수 있다. 최소 100개의 서로 다른 기본 과제가 필요하면 `samples_per_cell >= 100`을 유지한다. 여러 Budget/Horizon 셀에 같은 ID가 반복되는 것은 비교용 재사용이므로 고유 sample 수로 중복 계산하지 않는다.

### Gemini CountTokens로 같은 pilot 재생성

이 절차는 `experiments/configs/pilot.json`을 그대로 사용하면서 History와 Memory의 token 측정만 Gemini CountTokens로 수행한다. Agent가 답변을 생성하거나 Tool 행동을 수행하는 벤치마크는 실행하지 않는다. seed 2026, sample 100개, Budget 2048, Horizon 50, History 목표 25000, Noise 목표 0.5, 저장된 Constraint 3개와 State update 2회는 유지한다. 적용 제약은 현재 조회·과거 조회에서 0개, 승인 과제에서 1개다.

먼저 해당 Python 환경에 `requirements.txt`의 의존성이 설치되어 있어야 한다. API key는 로컬의 `GEMINI_API_KEY` 환경변수로 설정하고, 프로젝트에서 확인한 정확한 모델명을 아래 `MODEL` 자리에 넣는다. API key를 문서, 실행 로그나 Git에 기록하지 않는다.

```powershell
python -m pip install -r requirements.txt
$geminiModel = "MODEL"
if (-not $env:GEMINI_API_KEY) { throw "현재 PowerShell 세션에 GEMINI_API_KEY를 설정하세요." }
python -m benchmark.generator --config experiments/configs/pilot.json --output benchmark/generated/pilot_gemini --gemini-token-model $geminiModel --gemini-token-cache-size 4096 --gemini-token-cache .cache/pilot_gemini_token_cache.sqlite3 --gemini-token-retries 3 --gemini-token-retry-base-seconds 0.5
```

CLI는 `.env` 파일을 자동으로 읽지 않는다. `.env`에 key가 있는 경우에도 현재 프로세스 환경변수로 내보내거나 `.env`를 읽는 로컬 실행 wrapper를 사용해야 한다. `GeminiTokenCounter()` 자체는 모델 인자가 없을 때 `GEMINI_TOKEN_MODEL`을 읽을 수 있지만, 현재 생성기 CLI는 `--gemini-token-model` 옵션이 있어야 Gemini counter를 선택한다. 환경변수의 모델명이나 `pilot.json`의 `model.name`만 바꾸고 이 옵션을 생략하면 Gemini 측정으로 전환되지 않는다.

출력 경로를 `pilot_gemini`로 분리해 기존 provisional 생성물을 덮지 않는다. 현재 `.gitignore`는 이 생성 경로와 `.cache/`를 제외하므로 로컬 재생성만으로 Git 커밋 대상이 되지는 않는다. Counter가 달라지면 같은 조건과 ID를 유지해도 padding 길이, Memory 비용과 실제 대화 내용은 달라질 수 있다. 비교할 때는 같은 코드 버전에서 만든 provisional 생성물을 사용한다. 구버전 생성물은 Noise 정의나 적용 제약 규칙도 다를 수 있다.

`--gemini-token-cache-size`는 프로세스 안의 LRU 캐시 크기이며, `--gemini-token-cache`는 모델명과 원문 SHA-256을 기준으로 token 수를 저장하는 SQLite 파일이다. 성공한 API 응답은 캐시에 저장되어 재실행에서 재사용할 수 있다. `--gemini-token-retries 3`은 재시도 가능한 오류에 최초 호출 후 최대 3회 재시도를 허용하고, `--gemini-token-retry-base-seconds 0.5`는 지수 backoff의 기준 간격이다. 인증 오류나 지속되는 호출 한도 오류는 해결되지 않을 수 있으며, 재시도 종료 후에는 오류로 중단한다. 같은 문장을 padding 탐색에서 여러 번 측정하므로 100개 sample당 API 호출이 100회로 제한되는 것은 아니다.

생성 명령이 정상 종료되면 같은 PowerShell 세션에서 저장된 결과를 확인한다.

```powershell
$manifest = Get-Content -LiteralPath benchmark/generated/pilot_gemini/manifest.json -Encoding UTF8 -Raw | ConvertFrom-Json
$cellPath = Join-Path benchmark/generated/pilot_gemini $manifest.cells[0].path
$dataset = Get-Content -LiteralPath $cellPath -Encoding UTF8 -Raw | ConvertFrom-Json
$manifest | Select-Object n_cells, n_scenarios, token_count_mode
$dataset.scenarios | Group-Object token_count_mode | Select-Object Count, Name
$dataset.scenarios | ForEach-Object { $_.metadata } | Measure-Object actual_history_length_tokens -Minimum -Maximum
$dataset.scenarios | ForEach-Object { $_.metadata } | Measure-Object actual_noise_memory_ratio -Minimum -Maximum
```

기대값은 셀 1개, sample 100개, manifest의 `token_count_mode`가 `gemini:MODEL`이고 시나리오 그룹도 `Count=100`, `Name=gemini:MODEL`인 것이다. 여기서 `MODEL`은 지정한 실제 모델명이다. 각 시나리오의 `metadata.token_count_mode`도 같아야 한다. `actual_history_length_tokens`는 24750~25250, `actual_noise_memory_ratio`는 0.49~0.51 범위이며 요청값 `history_length_tokens=25000`, `noise_memory_ratio=0.5`와 따로 확인한다.

이 표시는 어떤 counter를 사용했는지에 대한 기록이다. 실제 API 연결 근거를 남기려면 실행에서 사용한 counter의 `metadata()` 또는 `stats()`로 API 호출, 메모리 hit, persistent hit, retry 수를 함께 보존한다. `api_calls`에는 실패한 호출 시도도 포함된다. 기존 캐시의 값만 재사용한 실행과 새 API 응답으로 측정한 실행을 구분하며, 필요하면 별도 캐시 경로를 지정한다. 이 가이드의 명령과 기대값 자체는 pilot의 실제 Gemini 측정 성공을 의미하지 않는다.

### 확인한 Gemini pilot 실행 결과

커밋 `f74d01859894e023f851548ca29d12b04e1024b2`의 생성기와 기존 `pilot.json`으로 실제 Gemini CountTokens 재생성 및 검증을 완료했다. 측정 모델은 기존 `smoke_gemini`와 같은 `gemini-3.8-flash`, SDK는 `google-genai 2.34.0`을 사용했다. `pilot.json`, `generator.py`, `context/tokenizer.py`, `runner.py`는 변경하지 않았다. 새 측정 기준에 맞추어 padding을 다시 생성했으며 Agent·Planner·Tool 행동 벤치마크는 실행하지 않았다.

| 확인 항목 | 결과 |
|---|---|
| 측정 표기 | `gemini:gemini-3.8-flash` |
| sample / 고유 ID | 100개 / 100개 |
| 유형별 개수 | 현재 조회 25, 과거 조회 25, 승인 요청 25, 승인된 발송 25 |
| 실제 History token 범위 | 25145~25152, 목표 25000의 1% 이내 |
| 실제 Noise 비율 범위 | 약 0.499608~0.500391, 목표 0.5의 허용 오차 이내 |
| 생성 counter의 API 호출 기록 | 3984회, persistent hit 0회, retry 0회 |
| 별도 캐시 없는 API 호출 기록 | 연결 검사 1회 + 대표 이력 4개 + 해당 Memory 41개 = 46회 |
| 별도 재측정 결과 | 네 유형에서 한 sample씩, 이력과 모든 Memory의 저장 token 수 일치 |
| 설정·스키마·정답 확인 | 기존 설정 유지, Memory 스키마·버전·시간·파일·승인 정답 검사 통과 |

같은 최신 코드로 provisional 데이터를 메모리에서 별도 생성해 비교했을 때도 100개 과제의 ID·질문·요구 State·기대 행동·승인 정답은 동일했다. 이 검증은 구버전의 커밋된 pilot 생성물과 비교한 것이 아니다. 전체 100개는 Gemini counter로 생성했고, 그중 네 유형의 대표 4개를 캐시 없이 독립 재측정했다는 범위로 해석한다.

로컬 산출물은 다음과 같다. 전달용 ZIP에는 셀 JSON, manifest와 검증 보고서 세 파일이 들어 있으며 압축 전 파일과 바이트 단위로 일치한다.

```text
benchmark/generated/pilot_gemini/cell_01e2ddce9162.json
benchmark/generated/pilot_gemini/manifest.json
benchmark/generated/pilot_gemini/verification.json
benchmark/generated/pilot_gemini.zip
```

`verification.json`에는 사용한 코드 커밋, config SHA-256, SDK 버전, counter 통계, 독립 재측정 sample과 추가 정답 검증 결과를 보존했다. 이 생성 경로들은 현재 Git 제외 대상이므로 자동으로 커밋되지는 않는다. API key는 데이터·보고서·문서에 기록하지 않았다.

출력 디렉터리의 `manifest.json`에는 셀 파일 경로, 시나리오 수, 사용한 설정과 token 측정 방식이 기록된다. 각 셀 JSON은 최상위 `scenarios` 배열을 가지며, 해당 셀의 모든 seed에 대한 입력을 포함한다. `samples_per_cell`은 **seed당 전체 sample 수**이다. strategy와 repeat는 데이터 복제 축이 아니다.

`main.json`은 12개의 Budget × Horizon 셀을 생성한다. 셀마다 두 seed의 입력이 들어가므로 40개 시나리오이며, 전체 셀에 걸쳐 480개 입력 레코드이다. 각 seed/셀의 배정은 다음과 같다.

| Task | State scope 또는 승인 상태 | 개수 |
|---|---|---:|
| `temporal_update` | 현재 State 요청 | 5 |
| `temporal_update` | 과거 시점의 State 요청 | 5 |
| `approval` | 승인 전 요청 및 이미 승인된 발송의 별도 snapshot | 합계 10 |

`smoke.json`은 한 seed, 한 셀에 총 5개를 생성한다. sample 수가 작은 설정에서도 현재·과거 요청과 승인 행동을 확인할 수 있다. `stress.json`과 `ablation.json`의 추가 축은 필수 Budget × Horizon 분석과 구분한다. `reference.json`은 큰 예산의 별도 참고 조건이며 제한 Budget 비교에 합치지 않는다.

같은 seed와 기본 Task의 `sample_id` 및 `scenario_id`는 Budget, Horizon, strategy, repeat와 무관하게 유지한다. Budget만 다른 조건에서는 원본 History와 정답을 공유한다. Horizon을 바꾸는 조건에서는 중요한 정보의 위치를 조절하면서 Task 내용, 정답, 버전 관계와 총 History 길이를 유지한다.

## History와 Memory 측정 기준

History는 중간 turn을 생략한 핵심 사건 목록이 아니라 연속된 합성 대화이다. `memory_snapshot`은 이력에서 보존할 정보를 요약한 입력으로, State의 이전 버전과 대체 참조를 유지한다. 같은 사실을 늘려 쓴 History와 요약된 Memory는 길이가 다르므로 두 비용을 혼용하지 않는다.

생성기는 주입된 counter로 History를 측정하여 목표 길이의 1% 이내인지 확인한다. 현재 Noise 비율은 별도로 생성한 synthetic noise note의 token 수를 `target_memory_ids`의 Memory token 수와 synthetic noise note token 수의 합으로 나눈 값이다. 계산식은 `synthetic_noise_tokens / (target_memory_tokens + synthetic_noise_tokens)`이며, 요청 비율과의 차이를 0.01 이내로 검증한다. 정답이 아닌 State 버전과 현재 행동에 적용되지 않는 Constraint는 이 비율에서 제외하고 `structural_distractor_tokens`로 따로 기록한다. 따라서 이 비율은 전체 snapshot 중 모든 무관 정보의 비율이나 History의 무관한 turn 비율을 뜻하지 않는다.

같은 Task의 Noise 조건을 비교할 때 target Memory의 비용이 함께 바뀌지 않도록, target Memory의 합계 비용을 `max(640, 기존 target 비용)`으로 맞춘 뒤 synthetic noise note의 길이를 조절한다. 이미 존재하는 synthetic note의 최소 비용 때문에 만족할 수 없는 낮은 Noise 비율이나, padding 후 필요한 정보를 담을 수 없는 짧은 History는 오류로 반환한다. `noise_memory_tokens`는 synthetic note 비용이며 `memory_tokens`는 target, synthetic note, structural distractor를 모두 합한 전체 snapshot 비용이다.

Horizon은 필수 State와 적용 가능한 Constraint, 이미 승인된 경우 승인 기록을 포함한 **Task 필수 정보 중 가장 가까운 정보**와 최종 Task의 turn 거리이다. 가장 가까운 필수 정보는 `current_turn - horizon_turns`에 두고, 나머지 필수 정보는 그보다 앞에 둔다. 과거 요청의 대상 시각은 Horizon을 바꾸어도 같은 버전을 가리키도록 유지한다. History 길이, 업데이트 수와 요청 Horizon을 함께 만족할 수 없는 입력은 정답을 바꾸어 생성하지 않고 오류로 알린다.

Metadata의 `history_length_tokens`와 `noise_memory_ratio`는 설정 grid의 요청값을 유지한다. 허용 오차 내 실측값 때문에 집계 셀이 나뉘지 않도록, 실제 측정은 `actual_history_length_tokens`와 `actual_noise_memory_ratio`로 따로 기록한다. 검토 시에는 요청값만 보지 말고 이 실측값, 중요 정보의 turn과 counter 측정 방식을 함께 확인한다. 기본 counter로 측정된 25K는 공백 기준 개발용 25K이며 실제 모델 기준 25K를 뜻하지 않는다.

현재 snapshot은 긴 대화를 적은 수의 기억으로 압축한 합성 입력이다. History가 25K라고 해서 검색 후보도 25K인 것은 아니다. 기본 Noise 0.5에서는 snapshot 비용이 최소 약 1,280 token이므로 낮은 Budget에서 선택 압력을 확인할 수 있지만, 큰 Budget에서는 전체 snapshot이 들어가 성능 차이가 줄 수 있다. 이 데이터만으로 모든 Budget에서 같은 난이도나 성능 차이를 보장하지 않는다. 실제 연구 실행에서는 제공한 Memory의 총 비용과 필수 비용, 선택 누락, 예산 중단 여부를 함께 확인한다.

## 행동 정답과 승인

`temporal_update`는 현재 값 또는 명시한 과거 시점에 유효한 파일로 `file.find_file`을 호출하도록 평가한다. `required_states[].state_scope`와 `expected_value`를 명시하며, 과거 요청에서는 `is_active`만으로 정답을 판단하지 않는다. State 변경 횟수가 0인 조건에는 경쟁 버전이 없으므로 temporal version 판단의 적용 여부도 구분한다.

`approval`은 올바른 보고서 파일을 `attachment`로 사용한 이메일 행동을 평가한다. 승인 전 snapshot은 `email.request_approval`이 정답이며 발송은 금지한다. 이미 승인된 snapshot은 실행 전에 허용된 승인 ID를 사용한 `email.send_email`이 정답이다. 승인 요청이 반환한 `pending` ID를 승인 완료로 취급하지 않는다. 승인 전후는 각각 독립 입력이며, 요청이 자동 승인으로 이어지는 시뮬레이션이 아니다.

생성된 모든 Constraint를 현재 행동에 적용되는 정답으로 지정하지 않는다. 현재 구현은 `temporal_update`의 파일 조회에는 적용 제약을 지정하지 않고, `approval`에는 첫 번째 승인 제약만 적용한다. 나머지 저장된 금지 파일 규칙은 현재 요청 파일과 다른 대상을 제한하므로 적용 제약 ID와 평가 규칙에서 제외한다. `constraint_rules`는 `applicable_constraint_ids`와 정확히 일치하며, 저장된 전체 Constraint 수와는 구분한다.

`query_context`, 정답 Memory ID, `required_states`, `constraint_rules`, 기대 Tool 호출은 평가용이다. 이 값을 실제 Agent나 retrieval의 정답 지름길로 전달하지 않는다. Runner는 실행 입력으로 query, session, snapshot, Budget과 현재 turn을 사용한다.

## 실행과 검증 범위

Runner에 전달할 때는 `manifest.json`에 기록된 **단일 셀 JSON**을 `--scenarios`로 지정한다. 셀을 합치면 비교 조건 간 공유된 ID가 중복되므로 모든 셀의 `scenarios`를 하나로 이어 붙이지 않는다. Runner의 나머지 필수 옵션은 다음 도움말로 확인한다.

```powershell
python -m benchmark.runner --help
```

현재 Runner는 단일 셀 JSON에 대해 Sliding Window, Vector Top-k, Utility-per-Token, Proposed와 repeat를 실행한다. Proposed는 외부 embedder와 각 Scenario의 ISO 8601 `current_time`을 요구하며 생성 데이터에는 이 값이 포함된다. `--proposed-ablations full no_constraint_preservation no_temporal no_state_mandatory no_budget_aware_packing`처럼 preset을 선택하면 동일 Scenario에서 각 변형을 실행한다. 여러 셀을 순회하는 전체 grid 실행, 실제 LLM Planner와 `reference.json`의 Long Context 실행은 별도 작업이다.

생성한 시나리오의 파일 목록과 사전 승인 ID를 mock Tool에 넣으려면 `--environment-agent-factory module:function`을 사용한다. 이 함수는 실행마다 해당 시나리오의 `environment` 사본만 받아 새 Agent를 반환한다. 정답, 검색 fixture와 기대 호출은 받지 않는다. 기존 인자 없는 `--agent-factory`와 동시에 지정하지 않는다.

예를 들어 프로젝트 루트의 `my_experiment.py`에 다음 함수를 두면 `--environment-agent-factory my_experiment:make_agent`로 지정할 수 있다.

```python
from agent.agent import Agent
from agent.tools.email import EmailTool
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool


def completed(response, calls, results):
    if not response or not results or not all(result.success for result in results):
        return False
    last = results[-1]
    if (last.tool_name, last.action) == ("file", "find_file"):
        return bool(last.output.get("found"))
    if (last.tool_name, last.action) == ("email", "request_approval"):
        return last.output.get("status") == "pending"
    if (last.tool_name, last.action) == ("email", "send_email"):
        return last.output.get("approved") is True
    return False


def make_agent(environment):
    return Agent(
        executor=ToolExecutor(
            file=FileTool(environment.get("files", {})),
            email=EmailTool(
                granted_approval_ids=environment.get("valid_approval_ids", [])
            ),
        ),
        goal_checker=completed,
    )
```

명시적인 `goal_checker`가 없으면 Agent의 `goal_completed`는 기본적으로 false이다. 위 예시는 Tool 작업의 종료 조건을 정의하며, 정확한 파일·적용 제약·State 사용은 이후 Evaluator가 별도로 검사한다. System Prompt와 Tool 정의 파일 등 다른 필수 Runner 인자도 지정해야 한다. 기본 결정적 Planner는 연결 검증용이며 실제 LLM의 시간 해석 능력을 측정하지 않는다.

생성기 및 기존 fixture의 계약 검증은 다음 명령으로 실행한다.

```powershell
python -m unittest discover -s tests -p "test_generator.py" -v
python -m unittest discover -s tests -p "test_scenarios.py" -v
```

Schema, 버전 참조, 정답 호환성과 잘못된 행동의 실패 판정은 입력 계약 검증이다. 결정적 Planner, provisional counter 또는 정답 snapshot으로 검증한 결과를 실제 LLM의 Task Success Rate나 연구 성능 결과로 보고하지 않는다.
