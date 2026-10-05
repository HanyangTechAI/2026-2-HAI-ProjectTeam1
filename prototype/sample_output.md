# Demo sample output

저장소 루트에서 실행한 `python prototype/run_demo.py`의 출력이다. LLM을 호출하지 않는다. 기억 선택기의 빈 함수는 이 데모 안에서만 보고서 이력으로 고정했다. `memory/` 구현은 바꾸지 않았다. 아래 수치는 실험 결과가 아니다.

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
hardcoded memory
  status: ok
  selected: ['approval', 'final']
  rejected: ['v1', 'v2']
  prompt: - 외부 이메일은 승인 후 발송한다.
  prompt: - report_final.pdf
  tools
  file.select_file -> success=True output={'filename': 'report_final.pdf', 'found': True}
  email.request_approval -> success=True output={'approval_id': 'approval_001', 'recipient': 'professor@example.test', 'subject': '최신 보고서', 'body': 'report_final.pdf를 첨부합니다.', 'attachment': 'report_final.pdf', 'status': 'pending'}
  email.send_email -> success=True output={'message_id': 'message_001', 'recipient': 'professor@example.test', 'subject': '최신 보고서', 'body': 'report_final.pdf를 첨부합니다.', 'attachment': 'report_final.pdf', 'approval_id': 'approval_001', 'approved': True}
  response: 보고서를 고르고 승인 후 발송했습니다.
  goal_completed: True
S13
  email.request_approval -> success=True output={'approval_id': 'approval_001', 'recipient': 'professor@example.test', 'subject': None, 'body': None, 'attachment': 'report_final.pdf', 'status': 'pending'}
  response: 시나리오의 기대 툴 호출을 마쳤습니다.
S13 task_success: True stopped: completed failures: ['retrieval_failure', 'temporal_failure']
demo ok
```

출력은 네 부분이다.

1. 손으로 쓴 컨텍스트에서 `report_final.pdf`를 고르고, 승인을 받은 뒤 보낸다. 발급된 `approval_001`을 평가 시나리오 사본에 넣으면 기존 evaluator가 성공으로 판정한다.
2. 예산이 첫 프롬프트와 같으면 파일 선택 한 번 뒤에 멈춘다. 예산이 1이면 고정 입력만으로도 넘어서 툴을 호출하지 않는다. `fixed_input_tokens` 43은 공백으로 나눈 단어 수이며, 모델 토크나이저 측정값이 아니다.
3. 하드코딩된 기억은 승인 제약과 최신 파일만 고르고 `report_v1.pdf`, `report_v2.pdf`는 제외한다. 그 두 문장이 프롬프트에 들어간 뒤 같은 세 호출이 성공한다. 행동을 고른 것은 여전히 스크립트다.
4. S13은 파일의 기대 호출대로 승인만 요청한다. `retrieval_failure`와 `temporal_failure`는 그 재현이 기억 선택기를 돌리지 않아서 남는다.
