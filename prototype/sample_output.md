# Demo sample output

저장소 루트에서 실행한 `python prototype/run_demo.py`의 출력이다. LLM은 호출하지 않는다. planner 자리에는 데모 스크립트가 들어가고, 기억 선택은 이 파일 안에서만 고정한다. `memory/` 구현은 바꾸지 않았다. 아래 수치는 실험 결과가 아니다.

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
  response: 외부 이메일은 승인 후 발송한다.
report_final.pdf
S13 status: ok task_success: True stopped: completed selected: ['approval', 'final'] failures: []
demo ok
```

출력은 네 부분이다. 행동을 고른 것은 모두 데모 스크립트다.

1. 손으로 쓴 컨텍스트에서 스크립트가 `report_final.pdf`를 고르고, 승인을 받은 뒤 보낸다. 발급된 `approval_001`을 평가 시나리오 사본에 넣으면 기존 evaluator가 성공으로 판정한다.
2. 예산이 첫 프롬프트와 같으면 파일 선택 한 번 뒤에 멈춘다. 예산이 1이면 고정 입력만으로도 넘어서 툴을 호출하지 않는다. `fixed_input_tokens` 43은 공백으로 나눈 단어 수이며, 모델 토크나이저 측정값이 아니다.
3. 보고서 이력은 이 데모 안에서 승인 제약과 최신 파일만 남기고 `report_v1.pdf`, `report_v2.pdf`는 빼도록 고정했다. 그 두 문장이 프롬프트에 들어간 뒤, 같은 스크립트가 세 호출을 실행한다.
4. S13의 기억은 시나리오 스냅샷에서 승인 제약과 `report_final.pdf`로 고정했다. 툴 호출은 planner가 고른 것이 아니라 시나리오의 기대 호출을 그대로 실행한 것이다. 응답 두 줄은 그 기억 문장이다. 고른 기억과 검색 목록을 evaluator에 넘기므로 검색 실패와 버전 실패는 남지 않는다. `task_success: True`는 그 재현이 정답과 같다는 뜻이다.
