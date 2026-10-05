# Demo sample output

저장소 루트에서 실행한 출력이다. LLM을 호출하지 않고, 기억 선택 파이프라인도 연결되어 있지 않다. 시나리오 실행은 `benchmark/scenarios.json`의 `expected_tool_calls`를 mock 툴로 재현한 것이다. 아래 수치는 실험 결과가 아니다.

## `python prototype/run_demo.py`

인자 없이 실행하면 스켈레톤 확인 뒤에 S13을 돌린다.

```text
scripted context
  file.select_file -> success=True output={'filename': 'report_final.pdf', 'found': True}
  email.request_approval -> success=True output={'approval_id': 'approval_001', 'recipient': 'professor@example.test', 'subject': '최신 보고서', 'body': 'report_final.pdf를 첨부합니다.', 'attachment': 'report_final.pdf', 'status': 'pending'}
  email.send_email -> success=True output={'message_id': 'message_001', 'recipient': 'professor@example.test', 'subject': '최신 보고서', 'body': 'report_final.pdf를 첨부합니다.', 'attachment': 'report_final.pdf', 'approval_id': 'approval_001', 'approved': True}
  response: 보고서를 고르고 승인 후 발송했습니다.
evaluator task_success: True
tight budget stop: context_budget calls=1
budget status: invalid_budget_configuration
budget tool calls: 0
usage: {'fixed_input_tokens': 43, 'input_tokens': None, 'output_tokens': 0}
S13
  email.request_approval -> success=True output={'approval_id': 'approval_001', 'recipient': 'professor@example.test', 'subject': None, 'body': None, 'attachment': 'report_final.pdf', 'status': 'pending'}
  response: 시나리오의 기대 툴 호출을 마쳤습니다.
S13 task_success: True stopped: completed failures: ['retrieval_failure', 'temporal_failure']
demo ok
```

앞부분은 손으로 쓴 Context에서 파일을 고르고, 승인을 받은 뒤 발송한다. 예산이 첫 프롬프트와 같으면 파일 선택 한 번 뒤에 멈추고, 예산이 1이면 툴을 호출하지 않는다. `fixed_input_tokens` 43은 공백으로 나눈 단어 수이며, 모델 토크나이저 측정값이 아니다.

S13은 파일에 적힌 기대 호출대로 승인만 요청한다. `retrieval_failure`와 `temporal_failure`는 기억 선택기를 돌리지 않아서 남는다. `task_success`는 행동 판정이다.

## `python prototype/run_demo.py S01`

시나리오 ID를 주면 그 시나리오만 읽는다.

```text
S01
  file.find_file -> success=True output={'filename': 'report_final.pdf', 'found': True}
  response: 시나리오의 기대 툴 호출을 마쳤습니다.
S01 task_success: True stopped: completed failures: ['retrieval_failure', 'temporal_failure']
demo ok
```

## `python prototype/run_demo.py --all`

재현할 수 있는 시나리오는 기대 툴 호출을 실행한다. 다음은 실행하지 않고 건너뛴 항목이다.

```text
S09 skip: no expected_tool_calls
S10 skip: no expected_tool_calls
S15 skip: benchmark_enabled=false
S17 skip: no expected_tool_calls
S20 skip: email.send_email missing a required argument: 'recipient'
S21 skip: benchmark_enabled=false
S23 skip: no expected_tool_calls
S24 skip: expected_pipeline_status=insufficient_context_budget
S25 skip: expected_pipeline_status=insufficient_context_budget
S26 skip: expected_pipeline_status=insufficient_context_budget
S27 skip: expected_pipeline_status=invalid_budget_configuration
S28 skip: no expected_tool_calls
S29 skip: no expected_tool_calls
S30 skip: no expected_tool_calls
S31 skip: no expected_tool_calls
S32 skip: expected_pipeline_status=insufficient_context_budget
S37 skip: no expected_tool_calls
S39 skip: no expected_tool_calls
```

나머지 S01–S08, S11–S14, S16, S18, S19, S22, S33–S36, S38은 `task_success: True`로 끝났다. `--list`는 시나리오마다 실행할지, 건너뛰는 이유가 무엇인지 보여 준다.
