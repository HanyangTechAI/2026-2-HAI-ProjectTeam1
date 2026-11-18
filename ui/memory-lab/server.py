"""Local Memory Lab server. Run from the repo root: python -m ui.memory-lab.server"""
import json
import os
from agent.llm_planner import LLMRunConfig
import re
from functools import lru_cache
from hashlib import blake2s
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from benchmark.experiment_runtime import completed_action, environment_agent
from benchmark.generator import generate_dataset, generate_scenario
from benchmark.runner import STRATEGIES, _run_one, _with_shared_planner
from context.tokenizer import GeminiTokenCounter, provisional_token_count, token_count_mode
from memory.ablation import ABLATION_PRESETS

UI = Path(__file__).resolve().parent
ROOT = UI.parents[1]


def load_gemini_key(path):
    """Use the project .env when the server has no Gemini key in its environment."""
    if os.environ.get("GEMINI_API_KEY") or not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "GEMINI_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            if value:
                os.environ["GEMINI_API_KEY"] = value
            return


MAIN = json.loads((ROOT / "experiments/configs/main.json").read_text("utf8"))
GRID = MAIN["grid"]
BUDGETS = GRID["context_token_budget"]
HORIZONS = GRID["horizon_turns"]
DEFAULT_BUDGET = 2048 if 2048 in BUDGETS else BUDGETS[0]
DEFAULT_HORIZON = 50 if 50 in HORIZONS else HORIZONS[0]
# The generator shares scenario content across budgets. Build the preview cell once.
preview_grid = {**GRID, "context_token_budget": [DEFAULT_BUDGET], "horizon_turns": [DEFAULT_HORIZON]}
preview_cell, = generate_dataset({**MAIN, "grid": preview_grid})["cells"]
SCENARIOS = {s["scenario_id"]: s for s in preview_cell["scenarios"]}
CONFIG_ID = blake2s(json.dumps(MAIN, sort_keys=True).encode(), digest_size=8).hexdigest()
PROMPTS = [(ROOT / "experiments/prompts" / name).read_text("utf8")
           for name in ("system_v1.txt", "tools_v1.txt")]


@lru_cache(maxsize=256)
def lexical_vector(text):
    """Portable deterministic word-frequency vectors for the local demonstration."""
    vector = [0] * 512
    for word in re.findall(r"\w+", text.casefold()):
        digest = blake2s(word.encode(), digest_size=4).digest()
        vector[int.from_bytes(digest, "big") % len(vector)] += 1
    return tuple(vector)


@lru_cache(maxsize=16)
def scenario_at_horizon(scenario_id, horizon):
    original = SCENARIOS[scenario_id]
    m = original["metadata"]
    if horizon is None or horizon == m["horizon_turns"]:
        return original
    # Each history turn takes at least one token; the generator builds >= 2H turns.
    if type(horizon) is not int or not 1 <= horizon <= m["history_length_tokens"] // 2:
        raise ValueError("Horizon이 현재 이력 길이에서 가능한 범위를 벗어났습니다.")
    try:
        return generate_scenario(
            horizon_turns=horizon, context_budget=original["context_budget"],
            approval_status=m["requested_approval_status"], state_scope=m["requested_state_scope"],
            **{k: m[k] for k in ("task_type", "seed", "sample_index", "history_length_tokens",
                                  "noise_memory_ratio", "constraint_count", "state_update_count",
                                  "scenario_profile", "primary_topic", "topic_index",
                                  "topic_variant_index", "horizon_generation_mode")},
        )
    except ValueError as exc:
        raise ValueError("이 시나리오의 사건 순서와 고정 이력 길이로 구성할 수 없는 Horizon입니다.") from exc


def scenario_preview(s):
    return dict(id=s["scenario_id"], seed=s["metadata"]["seed"], sample=s["metadata"]["sample_index"], query=s["query"], turn=s["current_turn"],
                horizon=s["metadata"]["horizon_turns"], topic=s["metadata"]["primary_topic"],
                task=s["metadata"]["task_type"],
                history=[dict(turn=m["turn_id"], text=m["content"][:180])
                         for m in s["memory_snapshot"] if m["memory_type"] in ("state", "constraint")])


def execute(scenario_id, budget, preset, planner="rules", horizon=None):
    config = LLMRunConfig() if planner == "gemini" else None
    factory = _with_shared_planner(environment_agent, config) if config else environment_agent
    counter = provisional_token_count
    if config:
        counter = GeminiTokenCounter(model=config.model, persistent_cache_path=ROOT / ".cache/memory_lab_tokens.sqlite3")
        counter.configure_request(system_instruction=config.system_instruction, generation_config=config.planner().generation_config())
    scenario = {**scenario_at_horizon(scenario_id, horizon), "context_budget": budget}
    snapshot = [{**m, "token_count": counter(m["content"])} for m in scenario["memory_snapshot"]]
    scenario["memory_snapshot"] = snapshot
    strategy = "proposed" if preset in ABLATION_PRESETS else preset
    record = _run_one(scenario, strategy, 0, agent_factory=None,
                      environment_agent_factory=factory, embedder=lexical_vector,
                      system_prompt=PROMPTS[0], tool_definitions=PROMPTS[1],
                      token_counter=counter, token_count_mode=token_count_mode(counter),
                      top_k=5, metric_k=5, utility_weights=(0.6, 0.3, 0.1), max_steps=8,
                      goal_checker=completed_action, proposed_ablation=ABLATION_PRESETS.get(preset),
                      llm_config=config, request_token_safety_margin=32 if config else 0)
    run = record["run_result"]
    return dict(scenario=scenario_preview(scenario), planner=planner, model=config.model if config else None, preset=preset, success=record["evaluation"]["task_success"],
                checks=record["evaluation"]["success_checks"],
                expected_calls=record["scenario"].get("expected_tool_calls", []),
                diagnostics=dict(pool_tokens=sum(m["token_count"] for m in snapshot), pool_size=len(snapshot),
                                 expected=record["evaluation"]["expected_tool_calls_present"], goal=run["goal_completed"],
                                 tags=record["evaluation"]["failure_types"], budget=budget,
                                 peak=run["prompt_token_audit"]["max_actual_prompt_tokens"]),
                response=run.get("response"), stop=run["stopped_reason"], calls=run["tool_calls"], steps=[
                    dict(number=s["step"], fixed_tokens=s["fixed_tokens"],
                         mandatory=[{"id": item["memory_id"], "tokens": item["token_count"]}
                                    for group in ("protected", "states")
                                    for item in s["selection_result"][group]],
                         limit=run["effective_context_budget"],
                         retrieved=s["selection_result"]["retrieved_memory_ids"],
                         selected=s["context_result"]["selected_memory_ids"] if s["context_result"].get("context") else [],
                         input_tokens=s["context_result"]["total_input_tokens"] if s["context_result"].get("context") else None,
                         context=(s.get("context_result") or {}).get("context", ""),
                         action=s.get("action_result")) for s in run["steps"]])


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(UI), **kwargs)

    def reply(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/settings":
            return self.reply(dict(budgets=BUDGETS, horizons=HORIZONS, defaultBudget=DEFAULT_BUDGET,
                                   defaultHorizon=DEFAULT_HORIZON, configId=CONFIG_ID))
        if self.path == "/api/scenarios":
            return self.reply([scenario_preview(s) for s in SCENARIOS.values()])
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/run":
            return self.reply({"error":"Unknown endpoint"}, 404)
        if self.headers.get_content_type() != "application/json" or self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.reply({"error":"실험실에서 실행하세요."}, 403)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 4096:
                raise ValueError()
            req = json.loads(self.rfile.read(size))
            planner = req.get("planner", "rules")
            if planner not in ("rules", "gemini"):
                raise ValueError()
            sid, budget, preset = req["scenario"], req["budget"], req["preset"]
            if sid not in SCENARIOS or budget not in BUDGETS or preset not in (*ABLATION_PRESETS, *STRATEGIES):
                raise ValueError()
            horizon = req.get("horizon", SCENARIOS[sid]["metadata"]["horizon_turns"])
            if type(horizon) is not int:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            return self.reply({"error":"실험 조건을 확인하세요."},400)
        try:
            scenario_at_horizon(sid, horizon)
        except ValueError as exc:
            return self.reply({"error":str(exc)}, 400)
        if planner == "gemini" and not os.getenv("GEMINI_API_KEY"):
            return self.reply({"error":"서버에 GEMINI_API_KEY를 설정한 뒤 다시 시작하세요."}, 503)
        try:
            result = execute(sid,budget,preset,planner,horizon)
        except Exception as exc:
            if planner == "gemini":
                return self.reply({"error":"Gemini 호출 실패. API 키, 모델 접근 권한, 할당량 및 네트워크를 확인하세요."}, 502)
            return self.reply({"error":f"실행 오류: {exc}"},500)
        self.reply(result)


if __name__ == "__main__":
    load_gemini_key(ROOT / ".env")
    print("Memory Lab: http://127.0.0.1:4174", flush=True)
    ThreadingHTTPServer(("127.0.0.1",4174),Handler).serve_forever()
