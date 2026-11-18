# Memory Lab

프로젝트 루트에서 `python -m ui.memory-lab.server`를 실행하고 http://127.0.0.1:4174 를 연다. 다른 컴퓨터에서도 Python 프로젝트 전체와 벤치마크 실행 의존성이 필요하다.

서버는 시작할 때 `experiments/configs/main.json`에서 시드, 사례 수, 주제, 예산과 Horizon을 읽어 시나리오 목록을 생성한다. 시드당 사례 수를 바꾸려면 `dataset.samples_per_cell`을 수정하고 서버를 재시작한다.

전략 비교는 Sliding Window, Vector Top-k, Utility-per-Token, Proposed를, Ablation 실험은 Full Proposed와 네 가지 단일 제거 preset을 실행한다. 각 조건은 같은 Main 사례와 입력 예산, 독립적인 모의 환경을 사용한다. UI는 선택한 사례를 조건별로 한 번씩 실행한다. 로컬 기본값은 규칙 Planner, 단어 빈도 해시 임베딩, provisional 토큰 계산이다.

