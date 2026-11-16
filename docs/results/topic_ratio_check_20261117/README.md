# 정보 주제별 생성 비율 확인용 입력

2026-11-17, 기본 생성기 `topic_balanced_v3`로 생성한 독립 시나리오 40개다.
조건은 context budget 2048, horizon 100 turns, history 25000, noise ratio 0.5,
constraint count 3, state update count 2, seed 42·43이다.

| 주제 | 개수 | 비율 | 파일 조회 / 이메일 |
|---|---:|---:|---:|
| 제약 | 10 | 25% | 5 / 5 |
| 시간·버전 | 10 | 25% | 5 / 5 |
| 상태 | 10 | 25% | 5 / 5 |
| Flexible 근거 | 10 | 25% | 5 / 5 |

[전체 테스트 입력 ZIP](topic_ratio_check_20261117.zip)에는 전체 입력 JSON,
생성 설정, manifest, 간략 시나리오 목록과 비율 요약이 들어 있다.
집계는 [ratio_summary.json](ratio_summary.json)에서도 확인할 수 있다.
압축을 풀고 `ratio_summary.md`에서 각 파일의 용도를 확인한다.

ID 중복, 주제별 개수, 실제 필수 메모리 유형 및 정답 일관성을 검사했다.
시간 주제에도 STATE가 필요하므로 Required State 적용 시나리오 수는 20개다.
이는 하나씩 배정한 주제별 개수와 중복을 허용한 정보 유형 집계의 차이다.

토큰 수는 공백 기준 임시 계산(`provisional`)이다. 이 입력은 생성 비율 확인용이며
실제 LLM 실행이나 ablation 성공률 측정 결과를 포함하지 않는다. 실제 모델 실험은
ZIP 안의 config를 Gemini counter로 재생성해 별도 결과 경로에서 수행한다.
생성 방법은 [생성기 사용법](../../guides/generator-usage.md)을 참고한다.
