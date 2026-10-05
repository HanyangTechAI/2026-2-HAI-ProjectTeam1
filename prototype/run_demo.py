"""Mock 툴과 Agent 스켈레톤 데모.

기억 선택 파이프라인은 아직 연결되지 않았다. 이 스크립트는 다음을 확인한다.

1. 완성된 Context에서 최신 파일을 고르고, 그 파일을 첨부해 승인을 받은 뒤 발송한다.
2. 발급된 승인 ID를 평가 시나리오에 반영하면 기존 evaluator가 성공으로 판정한다.
3. 툴 결과를 붙인 프롬프트가 예산을 넘으면 다음 계획을 하지 않는다.
4. 고정 입력이 예산을 넘으면 툴을 호출하지 않는다.
5. benchmark/scenarios.json의 시나리오를 읽어, 기대 툴 호출을 mock 툴로 실행하고 evaluator에 넘긴다.

사용법:

    python prototype/run_demo.py
    python prototype/run_demo.py S13
    python prototype/run_demo.py S01 S14
    python prototype/run_demo.py --all
    python prototype/run_demo.py --list

인자 없이 실행하면 스켈레톤 확인 뒤에 S13을 돌린다. 시나리오 실행은 기대 툴 호출을
그대로 재현한다. LLM이 행동을 고르거나 기억 선택기가 memory_snapshot을 고르지는 않는다.
"""

from __future__ import annotations

import html
import inspect
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.agent import Agent, AgentAction, usage_dict
from agent.context import provisional_token_count
from agent.evaluation import scenario_with_granted_approvals
from agent.tools import CalendarTool, EmailTool, FileTool, TaskTool, ToolCall, ToolExecutor
from benchmark.evaluator import evaluate
import memory.selector as selector_module
from memory.query_analyzer import QueryContext, TemporalIntent
from memory.schema import MemoryItem, MemoryType
from memory.selector import MemoryCandidate, PipelineStatus
from memory.store import MemoryStore

SYSTEM_PROMPT = "제약이 있으면 그 제약을 지키고, 상태에 적힌 파일을 사용한다."
TOOL_DEFINITIONS = """
email.draft_email(recipient, subject, body, attachment=None)
email.request_approval(recipient, subject, body, attachment=None)
email.send_email(recipient, subject, body, approval_id=None, attachment=None)
file.find_file(filename)
file.select_file(filename)
file.delete_file(filename)
calendar.create_event(start_time, title=None, end_time=None)
calendar.find_event(query)
task.create_task(title)
task.update_task(task_id=None, status=None, assignee=None, owner=None)
""".strip()
TASK = "교수님께 최신 보고서를 보내줘."
CONTEXT = f"""
[SYSTEM]
{SYSTEM_PROMPT}

[TOOL DEFINITIONS]
{TOOL_DEFINITIONS}

[PROTECTED CONSTRAINTS]
- 외부 이메일은 승인 후 발송한다.

[STATE]
- 현재 보고서는 report_final.pdf이다.

[CURRENT TASK]
{TASK}
""".strip()
RECIPIENT = "professor@example.test"
SUBJECT = "최신 보고서"
BODY = "report_final.pdf를 첨부합니다."
ATTACHMENT = "report_final.pdf"
CONTEXT_BUDGET = 2000
SESSION_ID = "session_demo"
SCENARIOS_PATH = ROOT / "benchmark" / "scenarios.json"
RESULT_HTML = ROOT / "prototype" / "demo_result.html"
REPORT: list[dict] = []


def scripted_plan(context: str) -> AgentAction:
    """데모용 planner. 파일 선택, 승인 요청, 승인된 발송 순서로만 움직인다."""
    if "action=select_file" not in context:
        return AgentAction("file", "select_file", {"filename": ATTACHMENT}, None)
    if "action=request_approval" not in context:
        return AgentAction("email", "request_approval", _message(), None)
    if "action=send_email" not in context:
        arguments = _message()
        arguments["approval_id"] = _approval_id(context)
        return AgentAction("email", "send_email", arguments, None)
    return AgentAction(None, None, {}, "보고서를 고르고 승인 후 발송했습니다.")


def scripted_goal(response, calls, results) -> bool:
    """세 호출이 모두 성공하고 최신 파일이 승인 메일에 첨부된 경우만 완료다."""
    if response is None or len(calls) != 3 or len(results) != 3:
        return False
    if [call.action for call in calls] != ["select_file", "request_approval", "send_email"]:
        return False
    if not all(result.success for result in results):
        return False
    if calls[0].arguments.get("filename") != ATTACHMENT:
        return False
    if calls[1].arguments.get("attachment") != ATTACHMENT:
        return False
    return bool(results[-1].output.get("approved"))


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    scenarios = load_scenarios()
    if args == ["--list"]:
        _print_scenario_list(scenarios)
        return
    try:
        if not args:
            _run_skeleton()
            _run_scenarios(scenarios, ["S13"])
        elif args == ["--all"]:
            _run_scenarios(scenarios, [item["scenario_id"] for item in scenarios])
        else:
            _run_scenarios(scenarios, args)
    finally:
        if REPORT:
            _write_result_html()
    print("demo ok")
    print(f"result html: {RESULT_HTML}")


def _run_skeleton() -> None:
    agent = _agent()
    acted = agent.act(CONTEXT, context_budget=CONTEXT_BUDGET, session_id=SESSION_ID)
    _print_trace("scripted context", acted.response, acted.tool_calls, acted.tool_results)
    if not scripted_goal(acted.response, acted.tool_calls, acted.tool_results):
        raise SystemExit("scripted trace did not complete the task")
    if acted.stopped_reason != "completed":
        raise SystemExit(f"unexpected stop: {acted.stopped_reason}")
    if len(acted.interactions) != 3 or any(item.source != "tool" for item in acted.interactions):
        raise SystemExit("tool results were not converted to interactions")

    usage = {
        "fixed_input_tokens": agent.token_counter(CONTEXT),
        "input_tokens": acted.input_tokens,
        "output_tokens": acted.output_tokens,
    }
    if usage["fixed_input_tokens"] > usage["input_tokens"]:
        raise SystemExit("fixed prompt tokens exceed the prompt sent to the planner")
    if usage["input_tokens"] > CONTEXT_BUDGET:
        raise SystemExit("planner saw a prompt over the context budget")
    evaluation = evaluate(
        scenario_with_granted_approvals(_scenario(), acted.tool_results),
        {
            "status": "ok",
            "goal_completed": True,
            "response": acted.response,
            "tool_calls": [
                {"tool_name": call.tool_name, "action": call.action, "arguments": call.arguments}
                for call in acted.tool_calls
            ],
            "usage": usage,
        },
        strategy="proposed",
    )
    if not evaluation.task_success:
        raise SystemExit(f"evaluator rejected the demo: {evaluation.metrics.get('failure_types')}")
    print(f"evaluator task_success: {evaluation.task_success}")

    agent.executor.reset()
    tight_budget = agent.token_counter(CONTEXT)
    stopped = agent.act(CONTEXT, context_budget=tight_budget, session_id=SESSION_ID)
    print(f"tight budget stop: {stopped.stopped_reason} calls={len(stopped.tool_calls)}")
    if stopped.stopped_reason != "context_budget" or len(stopped.tool_calls) != 1:
        raise SystemExit("tool results must not be planned once they exceed the budget")
    if agent._goal_completed(stopped):
        raise SystemExit("a budget stop is not task success")

    agent.executor.reset()
    blocked = agent.run_task(TASK, SESSION_ID, context_budget=1)
    print(f"budget status: {blocked.status.value}")
    print(f"budget tool calls: {len(blocked.tool_calls)}")
    print(f"usage: {usage_dict(blocked.usage)}")
    if blocked.status != PipelineStatus.INVALID_BUDGET_CONFIGURATION:
        raise SystemExit("tiny budget must stop before planning")
    if blocked.tool_calls or blocked.goal_completed:
        raise SystemExit("budget failure must not call tools or count as success")
    _run_hardcoded_memory()
    _check_scenario_tool_shapes()


def _run_hardcoded_memory() -> None:
    """비어 있는 기억 단계를 이 데모의 보고서 이력으로 고정하고 run_task를 돌린다."""
    _install_hardcoded_selector()
    agent = Agent(
        _HardcodedStore(_demo_memories()),
        system_prompt=SYSTEM_PROMPT,
        tool_definitions=TOOL_DEFINITIONS,
        executor=ToolExecutor(file=FileTool({"report_v1.pdf": "old", "report_v2.pdf": "mid", ATTACHMENT: "final"})),
        planner=scripted_plan,
        goal_checker=scripted_goal,
    )
    result = agent.run_task(TASK, SESSION_ID, context_budget=CONTEXT_BUDGET)
    selection = result.selection_result
    print("hardcoded memory")
    print(f"  status: {result.status.value}")
    print(f"  selected: {list(selection.selected_memory_ids) if selection else []}")
    print(f"  rejected: {list(selection.rejected_memory_ids) if selection else []}")
    if result.context_result and result.context_result.context:
        for line in result.context_result.context.splitlines():
            if line.startswith("- "):
                print(f"  prompt: {line}")
    _print_trace("  tools", result.response, result.tool_calls, result.tool_results)
    print(f"  goal_completed: {result.goal_completed}")
    if result.status != PipelineStatus.OK or not result.goal_completed:
        raise SystemExit("hardcoded memory run did not finish the task")
    if list(selection.selected_memory_ids) != ["approval", "final"]:
        raise SystemExit(f"hardcoded selection was {selection.selected_memory_ids}")


class _HardcodedStore:
    """데모 한 번을 위한 고정 목록. MemoryStore 구현이 아니다."""

    def __init__(self, memories: list[MemoryItem]) -> None:
        self._memories = list(memories)

    def find_all_by_key(self, session_id: str, memory_key: str) -> list[MemoryItem]:
        return [
            item for item in self._memories
            if item.session_id == session_id and item.memory_key == memory_key
        ]

    def find_active_protected_constraints(self, session_id: str) -> list[MemoryItem]:
        return [
            item for item in self._memories
            if item.session_id == session_id
            and item.is_active
            and item.is_protected
            and item.memory_type == MemoryType.CONSTRAINT
        ]

    def list_session_memories(self, session_id: str) -> list[MemoryItem]:
        return [item for item in self._memories if item.session_id == session_id]


def _install_hardcoded_selector() -> None:
    """selector가 아직 던지는 함수만 데모 답으로 바꾼다."""
    selector_module.analyze_query = _hard_analyze
    selector_module.retrieve_candidates = _hard_retrieve
    selector_module.expand_version_chains = _hard_expand
    selector_module.resolve_temporal_versions = _hard_resolve
    selector_module.is_constraint_applicable = _hard_applicable
    selector_module.resolve_required_states = _hard_states
    selector_module.compute_utility = _hard_utility
    selector_module.select_flexible = _hard_flexible


def _hard_analyze(query: str, current_turn: int, current_time: datetime) -> QueryContext:
    return QueryContext(
        query=query,
        temporal_intent=TemporalIntent.CURRENT,
        target_time=current_time,
        entities=("보고서",),
        required_tools=("email",),
        current_turn=current_turn,
        current_time=current_time,
    )


def _hard_retrieve(query_context, memory_store, session_id: str, top_k: int) -> list[MemoryCandidate]:
    del query_context, top_k
    return [MemoryCandidate(item) for item in memory_store.list_session_memories(session_id)]


def _hard_expand(candidates, memory_store) -> list[MemoryCandidate]:
    expanded = list(candidates)
    seen = {item.memory.memory_id for item in expanded}
    for candidate in list(candidates):
        key = candidate.memory.memory_key
        if key is None:
            continue
        for memory in memory_store.find_all_by_key(candidate.memory.session_id, key):
            if memory.memory_id in seen:
                continue
            seen.add(memory.memory_id)
            expanded.append(MemoryCandidate(memory))
    return expanded


def _hard_resolve(candidates, query_context) -> list[MemoryCandidate]:
    del query_context
    return list(candidates)


def _hard_applicable(constraint: MemoryItem, query_context) -> bool:
    del query_context
    return constraint.is_active and constraint.is_protected


def _hard_states(candidates, query_context) -> list[MemoryItem]:
    del query_context
    return [
        candidate.memory
        for candidate in candidates
        if candidate.memory.memory_type == MemoryType.STATE and candidate.memory.is_active
    ]


def _hard_utility(candidate: MemoryCandidate, query_context) -> float:
    del query_context
    return candidate.memory.importance


def _hard_flexible(candidates, budget: int) -> list[MemoryCandidate]:
    del candidates, budget
    return []


def _demo_memories() -> list[MemoryItem]:
    def at(value: str) -> datetime:
        return datetime.fromisoformat(value)

    return [
        MemoryItem(
            "v1", SESSION_ID, 3, MemoryType.STATE, "report_v1.pdf", 30,
            at("2026-06-01T00:00:00+09:00"), at("2026-06-01T00:00:00+09:00"),
            at("2026-08-15T00:00:00+09:00"), memory_key="report.current_file",
            is_active=False, superseded_by="v2",
        ),
        MemoryItem(
            "v2", SESSION_ID, 60, MemoryType.STATE, "report_v2.pdf", 30,
            at("2026-08-15T00:00:00+09:00"), at("2026-08-15T00:00:00+09:00"),
            at("2026-09-20T00:00:00+09:00"), memory_key="report.current_file",
            is_active=False, supersedes="v1", superseded_by="final",
        ),
        MemoryItem(
            "final", SESSION_ID, 100, MemoryType.STATE, "report_final.pdf", 30,
            at("2026-09-20T00:00:00+09:00"), at("2026-09-20T00:00:00+09:00"),
            memory_key="report.current_file", is_active=True, supersedes="v2",
        ),
        MemoryItem(
            "approval", SESSION_ID, 3, MemoryType.CONSTRAINT, "외부 이메일은 승인 후 발송한다.", 60,
            at("2026-06-01T00:00:00+09:00"), at("2026-06-01T00:00:00+09:00"),
            memory_key="email.external.requires_approval", is_active=True, is_protected=True,
        ),
    ]


def load_scenarios(path: Path = SCENARIOS_PATH) -> list[dict]:
    """scenarios.json의 scenarios 배열을 읽는다. 실행 결과는 들어 있지 않다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise SystemExit(f"no scenarios in {path}")
    return scenarios


def _print_scenario_list(scenarios: list[dict]) -> None:
    for scenario in scenarios:
        reason = _replay_skip_reason(scenario)
        state = "replay" if reason is None else f"skip: {reason}"
        print(f"{scenario['scenario_id']}  {state}  {scenario.get('title', '')}")


def _run_scenarios(scenarios: list[dict], scenario_ids: list[str]) -> None:
    by_id = {item["scenario_id"]: item for item in scenarios}
    missing = [scenario_id for scenario_id in scenario_ids if scenario_id not in by_id]
    if missing:
        raise SystemExit(f"unknown scenario: {', '.join(missing)}")
    failed = [
        scenario_id
        for scenario_id in scenario_ids
        if not _run_one_scenario(by_id[scenario_id])
    ]
    if failed:
        raise SystemExit(f"scenario replay failed: {', '.join(failed)}")


def _run_one_scenario(scenario: dict) -> bool:
    """기대 툴 호출을 Agent.act로 실행하고 그 시나리오 원문으로 평가한다."""
    scenario_id = scenario["scenario_id"]
    reason = _replay_skip_reason(scenario)
    if reason is not None:
        print(f"{scenario_id} skip: {reason}")
        REPORT.append(_scenario_row(scenario, kind="skip", skip=reason))
        return True

    context = _scenario_context(scenario["query"])
    budget = int(scenario["context_budget"])
    fixed_tokens = provisional_token_count(context)
    if fixed_tokens > budget:
        raise SystemExit(f"{scenario_id} prompt is {fixed_tokens} tokens and budget is {budget}")

    expected = scenario["expected_tool_calls"]
    planner = _ExpectedCallPlanner(expected)
    agent = Agent(
        MemoryStore(),
        system_prompt=SYSTEM_PROMPT,
        tool_definitions=TOOL_DEFINITIONS,
        executor=_scenario_executor(scenario),
        planner=planner,
        goal_checker=_expected_call_goal(len(expected)),
        current_turn=int(scenario.get("current_turn", 0)),
        max_steps=len(expected) + 1,
    )
    acted = agent.act(context, context_budget=budget, session_id=scenario["session_id"])
    _print_trace(scenario_id, acted.response, acted.tool_calls, acted.tool_results)
    if acted.input_tokens < fixed_tokens:
        print(f"{scenario_id} stopped: {acted.stopped_reason}")
        REPORT.append(_scenario_row(
            scenario, kind="fail", calls=acted.tool_calls, results=acted.tool_results,
            response=acted.response, stopped=acted.stopped_reason,
        ))
        return False

    usage = {
        "fixed_input_tokens": fixed_tokens,
        "input_tokens": acted.input_tokens,
        "output_tokens": acted.output_tokens,
    }
    evaluation = evaluate(
        scenario_with_granted_approvals(scenario, acted.tool_results),
        {
            "status": "ok",
            "goal_completed": agent._goal_completed(acted),
            "response": acted.response,
            "tool_calls": [
                {"tool_name": call.tool_name, "action": call.action, "arguments": call.arguments}
                for call in acted.tool_calls
            ],
            "usage": usage,
        },
        strategy="proposed",
    )
    failures = evaluation.metrics.get("failure_types") or []
    print(
        f"{scenario_id} task_success: {evaluation.task_success} "
        f"stopped: {acted.stopped_reason} failures: {failures}"
    )
    REPORT.append(_scenario_row(
        scenario,
        kind="pass" if evaluation.task_success else "fail",
        calls=acted.tool_calls,
        results=acted.tool_results,
        response=acted.response,
        stopped=acted.stopped_reason,
        task_success=evaluation.task_success,
        failures=list(failures),
    ))
    return bool(evaluation.task_success)


def _replay_skip_reason(scenario: dict) -> str | None:
    """기억 선택이나 LLM 응답이 필요한 시나리오는 재현 대상에서 뺀다."""
    if scenario.get("benchmark_enabled") is False:
        return "benchmark_enabled=false"
    assertions = scenario.get("assertions") or {}
    if assertions.get("must_not_execute_agent") or assertions.get("must_not_execute_tools"):
        return f"expected_pipeline_status={scenario.get('expected_pipeline_status')}"
    if scenario.get("expected_pipeline_status") not in (None, "ok"):
        return f"expected_pipeline_status={scenario.get('expected_pipeline_status')}"
    if not scenario.get("expected_tool_calls"):
        return "no expected_tool_calls"
    incomplete = _incomplete_expected_call(scenario)
    if incomplete is not None:
        return incomplete
    return None


def _incomplete_expected_call(scenario: dict) -> str | None:
    """기대 인자가 mock 시그니처에 바인딩되지 않으면 재현할 수 없다."""
    tools = {
        "email": EmailTool,
        "file": FileTool,
        "calendar": CalendarTool,
        "task": TaskTool,
    }
    for call in scenario["expected_tool_calls"]:
        tool = tools.get(call["tool_name"])
        method = getattr(tool, call["action"], None) if tool is not None else None
        if method is None:
            return f"unknown tool call {call['tool_name']}.{call['action']}"
        try:
            inspect.signature(method).bind(object(), **dict(call.get("arguments") or {}))
        except TypeError as exc:
            return f"{call['tool_name']}.{call['action']} {exc}"
    return None


def _scenario_context(query: str) -> str:
    return f"""
[SYSTEM]
{SYSTEM_PROMPT}

[TOOL DEFINITIONS]
{TOOL_DEFINITIONS}

[CURRENT TASK]
{query}
""".strip()


def _scenario_executor(scenario: dict) -> ToolExecutor:
    granted = list((scenario.get("environment") or {}).get("valid_approval_ids") or [])
    return ToolExecutor(
        email=EmailTool(granted_approval_ids=granted),
        file=FileTool(_scenario_files(scenario)),
    )


def _scenario_files(scenario: dict) -> dict[str, str]:
    names: list[str] = []
    for call in scenario.get("expected_tool_calls", []):
        for value in (call.get("arguments") or {}).values():
            if isinstance(value, str) and _looks_like_filename(value):
                names.append(value)
    for item in scenario.get("memory_snapshot", []):
        content = item.get("content")
        if isinstance(content, str) and _looks_like_filename(content):
            names.append(content)
    return {name: name for name in names}


def _looks_like_filename(value: str) -> bool:
    return "." in value and " " not in value and len(value) < 80


class _ExpectedCallPlanner:
    """시나리오 expected_tool_calls를 순서대로 내놓는 planner."""

    def __init__(self, calls: list[dict]) -> None:
        self._calls = calls
        self._index = 0

    def __call__(self, context: str) -> AgentAction:
        if self._index >= len(self._calls):
            return AgentAction(None, None, {}, "시나리오의 기대 툴 호출을 마쳤습니다.")
        call = self._calls[self._index]
        self._index += 1
        return AgentAction(
            call["tool_name"],
            call["action"],
            dict(call.get("arguments") or {}),
            None,
        )


def _expected_call_goal(expected_count: int):
    def check(response, calls, results) -> bool:
        return (
            response is not None
            and len(calls) == expected_count
            and len(results) == expected_count
            and all(result.success for result in results)
        )

    return check


def _check_scenario_tool_shapes() -> None:
    """scenarios.json의 기대 인자만으로 툴이 동작하는지 확인한다."""
    executor = ToolExecutor(
        email=EmailTool(granted_approval_ids=["approved_001"]),
        file=FileTool({"contract_original.pdf": "contract"}),
    )
    sent = executor.execute(ToolCall("email", "send_email", {
        "recipient": "professor@example.test",
        "attachment": "report_final.pdf",
        "approval_id": "approved_001",
    }))
    if not sent.success or not sent.output.get("approved"):
        raise SystemExit("pre-granted approval_id was rejected")
    deleted = executor.execute(ToolCall("file", "delete_file", {"filename": "contract_original.pdf"}))
    if not deleted.success:
        raise SystemExit("delete_file is not available")
    event = executor.execute(ToolCall("calendar", "create_event", {
        "start_time": "2026-10-06T10:00:00+09:00",
    }))
    if not event.success or event.output.get("start_time") != "2026-10-06T10:00:00+09:00":
        raise SystemExit("create_event rejected a start_time-only call")
    updated = executor.execute(ToolCall("task", "update_task", {"assignee": "member_zelora"}))
    if not updated.success or updated.output.get("assignee") != "member_zelora":
        raise SystemExit("update_task rejected an assignee-only call")


def _agent() -> Agent:
    files = FileTool({"report_v1.pdf": "old", ATTACHMENT: "final"})
    return Agent(
        MemoryStore(),
        system_prompt=SYSTEM_PROMPT,
        tool_definitions=TOOL_DEFINITIONS,
        executor=ToolExecutor(file=files),
        planner=scripted_plan,
        goal_checker=scripted_goal,
    )


def _message() -> dict:
    return {
        "recipient": RECIPIENT,
        "subject": SUBJECT,
        "body": BODY,
        "attachment": ATTACHMENT,
    }


def _scenario() -> dict:
    return {
        "scenario_id": "demo_report",
        "context_budget": CONTEXT_BUDGET,
        "target_memory_ids": [],
        "required_state_ids": ["report_final"],
        "applicable_constraint_ids": ["approval_rule"],
        "required_states": [{
            "memory_id": "report_final",
            "tool_call": {"tool_name": "email", "action": "request_approval"},
            "argument": "attachment",
            "expected_value": ATTACHMENT,
            "stale_values": ["report_v1.pdf"],
        }],
        "constraint_rules": [{
            "constraint_id": "approval_rule",
            "approval": {
                "execution": {"tool_name": "email", "action": "send_email"},
                "request": {"tool_name": "email", "action": "request_approval"},
                "request_required": True,
                "valid_approval_ids": [],
            },
        }],
        "expected_tool_calls": [
            {"tool_name": "file", "action": "select_file", "arguments": {"filename": ATTACHMENT}},
            {"tool_name": "email", "action": "request_approval", "arguments": {"attachment": ATTACHMENT}},
            {"tool_name": "email", "action": "send_email", "arguments": {"attachment": ATTACHMENT}},
        ],
        "forbidden_tool_calls": [],
    }


def _approval_id(context: str) -> str:
    marker = "approval_id="
    start = context.rfind(marker)
    if start < 0:
        raise ValueError("approval result is missing from the context")
    start += len(marker)
    end = context.find(" ", start)
    if end < 0:
        raise ValueError("approval id was not terminated")
    return context[start:end]


def _scenario_row(
    scenario: dict,
    *,
    kind: str,
    skip: str | None = None,
    calls=(),
    results=(),
    response: str | None = None,
    stopped: str | None = None,
    task_success: bool | None = None,
    failures: list[str] | None = None,
) -> dict:
    return {
        "id": scenario["scenario_id"],
        "title": scenario.get("title") or "",
        "query": scenario.get("query") or "",
        "kind": kind,
        "skip": skip,
        "calls": [
            {"name": f"{call.tool_name}.{call.action}", "success": result.success, "output": dict(result.output), "error": result.error}
            for call, result in zip(calls, results)
        ],
        "response": response,
        "stopped": stopped,
        "task_success": task_success,
        "failures": failures or [],
    }


def _write_result_html() -> None:
    passed = sum(item["kind"] == "pass" for item in REPORT)
    skipped = sum(item["kind"] == "skip" for item in REPORT)
    failed = sum(item["kind"] == "fail" for item in REPORT)
    rows = "\n".join(_result_card(item) for item in REPORT)
    RESULT_HTML.write_text(f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>run_demo 결과</title>
<style>
  body {{ margin: 0; background: #f6f7f9; color: #1c1917; font-family: "Malgun Gothic", sans-serif; }}
  main {{ width: min(1100px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 48px; }}
  h1 {{ margin: 0 0 8px; font-size: 28px; }}
  .counts {{ display: flex; gap: 8px; margin: 16px 0 20px; }}
  .counts span {{ background: white; border: 1px solid #e7e5e4; border-radius: 999px; padding: 6px 12px; font-weight: 700; }}
  article {{ background: white; border: 1px solid #e7e5e4; border-radius: 12px; padding: 14px 16px; margin-top: 10px; }}
  .head {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; }}
  .id {{ font-size: 18px; }}
  .query {{ color: #57534e; margin-top: 4px; }}
  .badge {{ border-radius: 999px; padding: 2px 8px; font-size: 12px; font-weight: 700; white-space: nowrap; }}
  .pass {{ background: #e8f6ec; color: #166534; }}
  .skip {{ background: #f5f5f4; color: #57534e; }}
  .fail {{ background: #fde8e8; color: #991b1b; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
  th, td {{ text-align: left; vertical-align: top; padding: 6px 8px; border-top: 1px solid #f0eeec; }}
  th {{ width: 140px; color: #78716c; font-weight: 600; }}
  .call {{ margin-top: 8px; padding: 8px 10px; background: #fafaf9; border-radius: 8px; }}
  .call b {{ display: block; }}
</style>
</head>
<body>
<main>
  <h1>run_demo 결과</h1>
  <div class="counts">
    <span class="pass">통과 {passed}</span>
    <span class="skip">건너뜀 {skipped}</span>
    <span class="fail">실패 {failed}</span>
  </div>
  {rows}
</main>
</body>
</html>
""", encoding="utf-8")
    webbrowser.open(RESULT_HTML.as_uri())


def _result_card(item: dict) -> str:
    kind = item["kind"]
    label = {"pass": "통과", "skip": "건너뜀", "fail": "실패"}[kind]
    query = f'<p class="query">{html.escape(item["query"])}</p>' if item["query"] else ""
    if kind == "skip":
        body = f"<p>{html.escape(_skip_text(item['skip']))}</p>"
    else:
        calls = "\n".join(_call_block(call) for call in item["calls"]) or "<p>툴 호출 없음</p>"
        verdict = []
        if item["task_success"] is not None:
            verdict.append(f"행동 판정 {'통과' if item['task_success'] else '실패'}")
        if item["stopped"]:
            verdict.append(html.escape(_stopped_text(item["stopped"])))
        if item["failures"]:
            verdict.append("남긴 기록 " + ", ".join(html.escape(_failure_text(name)) for name in item["failures"]))
        response = f"<p>응답: {html.escape(item['response'])}</p>" if item["response"] else ""
        body = calls + response + (f"<p>{' · '.join(verdict)}</p>" if verdict else "")
    return f"""<article>
  <div class="head"><b class="id">{html.escape(item["id"])} {html.escape(item["title"])}</b><span class="badge {kind}">{label}</span></div>
  {query}
  {body}
</article>"""


def _call_block(call: dict) -> str:
    rows = "".join(
        f"<tr><th>{html.escape(_field_name(key))}</th><td>{html.escape(_field_value(value))}</td></tr>"
        for key, value in call["output"].items()
    )
    if call["error"]:
        rows += f"<tr><th>오류</th><td>{html.escape(str(call['error']))}</td></tr>"
    mark = "성공" if call["success"] else "실패"
    return f'<div class="call"><b>{html.escape(call["name"])} · {mark}</b><table>{rows}</table></div>'


def _field_name(key: str) -> str:
    return {
        "filename": "파일",
        "found": "목록에 있음",
        "recipient": "받는 사람",
        "subject": "메일 제목",
        "body": "본문",
        "attachment": "첨부",
        "approval_id": "승인 번호",
        "approved": "승인됨",
        "status": "상태",
        "message_id": "메시지 번호",
        "draft_id": "임시저장 번호",
        "event_id": "일정 번호",
        "title": "제목",
        "start_time": "시작",
        "end_time": "종료",
        "task_id": "작업 번호",
        "assignee": "담당자",
        "owner": "소유자",
    }.get(key, key)


def _field_value(value) -> str:
    if value is None:
        return "없음"
    if value is True:
        return "예"
    if value is False:
        return "아니오"
    if value == "pending":
        return "승인 대기"
    return str(value)


def _stopped_text(reason: str) -> str:
    return {
        "completed": "정상 종료",
        "context_budget": "예산 초과로 중단",
        "tool_failure": "툴 실패로 중단",
        "max_steps": "반복 한도로 중단",
    }.get(reason, reason)


def _skip_text(reason: str | None) -> str:
    if reason == "no expected_tool_calls":
        return "툴 호출 정답이 없어서 실행하지 않음"
    if reason == "benchmark_enabled=false":
        return "이번 벤치마크에서 제외된 시나리오"
    if reason == "expected_pipeline_status=insufficient_context_budget":
        return "필수 기억이 예산에 안 들어가서 실행하지 않음"
    if reason == "expected_pipeline_status=invalid_budget_configuration":
        return "고정 입력이 예산보다 커서 실행하지 않음"
    if reason and "recipient" in reason:
        return "기대 호출에 받는 사람 주소가 없어서 실행하지 않음"
    return reason or ""


def _failure_text(name: str) -> str:
    return {
        "retrieval_failure": "검색된 기억 없음",
        "temporal_failure": "맞춘 버전 기억 없음",
        "selection_failure": "선택에서 빠짐",
        "constraint_failure": "제약 위반",
        "approval_failure": "승인 실패",
        "budget_failure": "예산 초과",
        "state_omission": "상태 누락",
        "stale_state_usage": "옛 상태 사용",
        "state_error": "상태 값 오류",
    }.get(name, name)


def _print_trace(title, response, calls, results) -> None:
    print(title)
    for call, result in zip(calls, results):
        detail = f"output={result.output}" if result.success else f"error={result.error}"
        print(f"  {call.tool_name}.{call.action} -> success={result.success} {detail}")
    print(f"  response: {response}")


if __name__ == "__main__":
    main(sys.argv[1:])
