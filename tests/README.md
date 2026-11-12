# 테스트 안내

## 전체 테스트

저장소 루트에서 다음 명령으로 단위 및 통합 테스트를 실행합니다.

```powershell
python -m unittest discover -s tests -v
```

특정 영역만 실행하려면 예를 들어 다음처럼 지정할 수 있습니다.

```powershell
python -m unittest discover -s tests/unit/memory -t tests -v
```

## 디렉토리

| 위치 | 내용 |
|---|---|
| [`unit/agent/`](unit/agent/) | LLM planner와 provider 실패 처리 |
| [`unit/benchmark/`](unit/benchmark/) | evaluator, 시나리오, generator와 runner |
| [`unit/context/`](unit/context/) | Context Builder, tokenizer, token audit |
| [`unit/experiments/`](unit/experiments/) | freeze 설정, 실행 metadata, 사전 검증 |
| [`unit/memory/`](unit/memory/) | Memory Store, State, retrieval, selector, ablation |
| [`unit/pipeline/`](unit/pipeline/) | Pipeline 및 adapter 단위 계약 |
| [`integration/`](integration/) | Store·Context·Agent 연결 통합 및 smoke 테스트 |
| [`manual/`](manual/) | API 호출이 필요한 수동 preflight 및 smoke gate |

`manual/`의 스크립트는 전체 `unittest discover`에 포함되지 않습니다. 실제 API를 호출하므로 필요한 환경 설정을 확인한 뒤 개별 실행합니다.

## 수동 실행 게이트

```powershell
python tests/manual/preflight_1k2k.py prepare
python tests/manual/preflight_1k2k.py run --margin 32
python tests/manual/preflight_1k2k.py report --margin 32

python tests/manual/smoke_4k8k.py prepare
python tests/manual/smoke_4k8k.py run
python tests/manual/smoke_4k8k.py report
```
