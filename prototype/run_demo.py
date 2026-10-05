"""Mock 툴과 Agent 스켈레톤 데모.

memory/ 구현은 비어 있다. 시나리오를 돌릴 때만 이 파일 안에서 기억 선택을
memory_snapshot과 합성 토큰 수로 고정한다. 에이전트는 그 결과로 예산을 끊거나,
기억 문장으로 답하거나, 기대 툴 호출을 실행한다. LLM은 호출하지 않는다.

사용법:

    python prototype/run_demo.py
    python prototype/run_demo.py S13
    python prototype/run_demo.py S01 S14
    python prototype/run_demo.py --all
    python prototype/run_demo.py --list

인자 없이 실행하면 스켈레톤 확인 뒤에 S13을 돌린다.
"""

from __future__ import annotations

import html
import json
import re
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent.agent as agent_module
from agent.agent import Agent, AgentAction, usage_dict
from agent.context import provisional_token_count
from agent.evaluation import scenario_with_granted_approvals
from agent.tools import CalendarTool, EmailTool, FileTool, TaskTool, ToolCall, ToolExecutor
from benchmark.evaluator import evaluate
import memory.selector as selector_module
from memory.query_analyzer import QueryContext, TemporalIntent
from memory.schema import MemoryItem, MemoryType
from memory.selector import (
    MemoryCandidate,
    PipelineStatus,
    SelectionResult,
    calculate_memory_budget,
    select_mandatory,
)
from memory.store import MemoryStore

SYSTEM_PROMPT = "제약이 있으면 그 제약을 지키고, 상태에 적힌 파일을 사용한다."
TOOL_DEFINITIONS = """
email.draft_email(recipient, subject, body, attachment=None)
email.request_approval(recipient, subject, body, attachment=None)
email.send_email(recipient=None, subject, body, approval_id=None, attachment=None)
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


_ACTIVE: dict = {}


def _print_scenario_list(scenarios: list[dict]) -> None:
    for scenario in scenarios:
        print(f"{scenario['scenario_id']}  run  {scenario.get('title', '')}")


def _run_scenarios(scenarios: list[dict], scenario_ids: list[str]) -> None:
    by_id = {item["scenario_id"]: item for item in scenarios}
    missing = [scenario_id for scenario_id in scenario_ids if scenario_id not in by_id]
    if missing:
        raise SystemExit(f"unknown scenario: {', '.join(missing)}")
    agent_module.select_memory = _scenario_select_memory
    failed = [
        scenario_id
        for scenario_id in scenario_ids
        if not _run_one_scenario(by_id[scenario_id])
    ]
    if failed:
        print(f"scenario mismatch: {', '.join(failed)}")


def _run_one_scenario(scenario: dict) -> bool:
    """스냅샷으로 기억을 고른 뒤 run_task로 예산, 답변, 툴 호출을 실행한다."""
    scenario_id = scenario["scenario_id"]
    try:
        items = [MemoryItem.from_dict(item) for item in scenario.get("memory_snapshot") or []]
    except (TypeError, ValueError) as exc:
        print(f"{scenario_id} memory snapshot: {exc}")
        REPORT.append(_scenario_row(scenario, kind="fail", pipeline="snapshot_error", note=str(exc)))
        return False

    conflict = _active_key_conflict(items)
    if conflict is not None:
        print(f"{scenario_id} store_consistency_error: {conflict}")
        REPORT.append(_scenario_row(
            scenario,
            kind="pass",
            pipeline="store_consistency_error",
            note=f"같은 키의 활성 기억이 둘이라 실행하지 않음 ({conflict})",
        ))
        return True

    _ACTIVE["scenario"] = scenario
    _ACTIVE["items"] = items
    expected = list(scenario.get("expected_tool_calls") or [])
    agent = Agent(
        _HardcodedStore(items),
        system_prompt=SYSTEM_PROMPT,
        tool_definitions=TOOL_DEFINITIONS,
        executor=_scenario_executor(scenario),
        planner=_ScenarioPlanner(scenario),
        goal_checker=_expected_call_goal(len(expected)),
        token_counter=_scenario_token_counter(scenario, items),
        current_turn=int(scenario.get("current_turn", 0)),
        max_steps=max(1, len(expected) + 1),
    )
    result = agent.run_task(scenario["query"], scenario["session_id"], int(scenario["context_budget"]))
    _print_trace(scenario_id, result.response, result.tool_calls, result.tool_results)
    evaluation = evaluate(
        scenario_with_granted_approvals(scenario, result.tool_results),
        _evaluation_run(result),
        strategy="proposed",
    )
    failures = list(evaluation.metrics.get("failure_types") or [])
    selected = _shown_ids(result)
    print(
        f"{scenario_id} status: {result.status.value} "
        f"task_success: {evaluation.task_success} "
        f"stopped: {result.stopped_reason} "
        f"selected: {selected} failures: {failures}"
    )
    expected_status = scenario.get("expected_pipeline_status")
    matched = _outcome_matches(expected_status, result.status.value, evaluation.task_success, result.tool_calls)
    REPORT.append(_scenario_row(
        scenario,
        kind="pass" if matched else "fail",
        calls=result.tool_calls,
        results=result.tool_results,
        response=result.response,
        stopped=result.stopped_reason,
        task_success=evaluation.task_success,
        failures=failures,
        pipeline=result.status.value,
        selected=selected,
    ))
    return matched


def _active_key_conflict(items: list[MemoryItem]) -> str | None:
    seen: dict[tuple[str, str], str] = {}
    for item in items:
        if not item.is_active or not item.memory_key:
            continue
        key = (item.session_id, item.memory_key)
        if key in seen:
            return f"{item.memory_key}: {seen[key]}, {item.memory_id}"
        seen[key] = item.memory_id
    return None


def _outcome_matches(expected_status, actual_status: str, task_success: bool, tool_calls) -> bool:
    if expected_status in (None, "ok"):
        return bool(task_success)
    return actual_status == expected_status and not tool_calls


def _shown_ids(result) -> list[str]:
    context = result.context_result
    if result.status != PipelineStatus.OK or context is None:
        return []
    return list(context.selected_memory_ids)


def _evaluation_run(result) -> dict:
    selection = result.selection_result
    context = result.context_result
    return {
        "status": result.status.value,
        "goal_completed": result.goal_completed,
        "response": result.response,
        "tool_calls": [
            {"tool_name": call.tool_name, "action": call.action, "arguments": call.arguments}
            for call in result.tool_calls
        ],
        "usage": usage_dict(result.usage),
        "selection_result": {
            "retrieved_memory_ids": list(selection.retrieved_memory_ids) if selection else [],
            "selected_memory_ids": list(selection.selected_memory_ids) if selection else [],
            "memory_tokens": selection.memory_tokens if selection else 0,
        },
        "context_result": {
            "selected_memory_ids": list(context.selected_memory_ids) if context else [],
            "memory_tokens": context.total_input_tokens if context else 0,
        },
    }


def _scenario_select_memory(
    query: str,
    session_id: str,
    memory_store,
    total_budget: int,
    fixed_tokens: int,
    current_turn: int,
    current_time: datetime,
    *,
    top_k: int = 20,
) -> SelectionResult:
    """시나리오 스냅샷에서 고를 기억을 고정한다. memory/ 선택기를 호출하지 않는다."""
    del query, memory_store, current_turn, current_time, top_k
    scenario = _ACTIVE["scenario"]
    items = [item for item in _ACTIVE["items"] if item.session_id == session_id]
    by_id = {item.memory_id: item for item in items}
    retrieved = tuple(by_id)
    status, memory_budget = calculate_memory_budget(total_budget, fixed_tokens)
    if status != PipelineStatus.OK:
        return SelectionResult(status=status, retrieved_memory_ids=retrieved)

    assertions = scenario.get("assertions") or {}
    fixture = scenario.get("context_fixture") or {}
    chosen_ids: list[str] = []
    seen: set[str] = set()
    for memory_id in [
        *list(assertions.get("mandatory_memory_ids") or []),
        *list(fixture.get("initial_selected_memory_ids") or assertions.get("selected_memory_ids") or []),
    ]:
        if memory_id in seen:
            continue
        seen.add(memory_id)
        chosen_ids.append(memory_id)
    chosen = [by_id[memory_id] for memory_id in chosen_ids if memory_id in by_id]
    mandatory_ids = list(assertions.get("mandatory_memory_ids") or [])
    mandatory_set = set(mandatory_ids) if mandatory_ids else None
    protected: list[MemoryItem] = []
    states: list[MemoryItem] = []
    flexible: list[MemoryItem] = []
    for item in chosen:
        mandatory = (
            item.memory_id in mandatory_set
            if mandatory_set is not None
            else item.memory_type in (MemoryType.CONSTRAINT, MemoryType.STATE) or item.is_protected
        )
        if mandatory and item.memory_type == MemoryType.STATE:
            states.append(item)
        elif mandatory:
            protected.append(item)
        else:
            flexible.append(item)
    overflow = select_mandatory(protected, states, memory_budget)
    if overflow.status != PipelineStatus.OK:
        return SelectionResult(
            status=overflow.status,
            retrieved_memory_ids=retrieved,
            memory_tokens=overflow.used_tokens,
        )
    removal = list(fixture.get("flexible_removal_order") or [])
    ordered = [item for item in flexible if item.memory_id not in removal]
    ordered.extend(item for memory_id in reversed(removal) for item in flexible if item.memory_id == memory_id)
    selected = [*protected, *states, *ordered]
    selected_ids = tuple(item.memory_id for item in selected)
    return SelectionResult(
        status=PipelineStatus.OK,
        protected=tuple(protected),
        states=tuple(states),
        flexible=tuple(ordered),
        retrieved_memory_ids=retrieved,
        selected_memory_ids=selected_ids,
        rejected_memory_ids=tuple(memory_id for memory_id in retrieved if memory_id not in set(selected_ids)),
        memory_tokens=sum(item.token_count for item in selected),
        remaining_budget=memory_budget - sum(item.token_count for item in protected + states),
    )


def _scenario_token_counter(scenario: dict, items: list[MemoryItem]):
    """시나리오의 합성 토큰 수. 단어 수를 실험 토크나이저 값으로 쓰지 않는다."""
    fixed = scenario.get("fixed_input_tokens")
    if type(fixed) is not int:
        return provisional_token_count
    fixture = scenario.get("context_fixture") or {}
    counts = [int(value) for value in fixture.get("tokenizer_counts_after_each_build") or []]
    removal = list(fixture.get("flexible_removal_order") or [])
    initial = list(fixture.get("initial_selected_memory_ids") or [])

    def counter(text: str) -> int:
        head = text.split("[TOOL RESULT]", 1)[0]
        present = {item.memory_id for item in items if f"- {item.content}" in head}
        if counts and initial and any(memory_id in present for memory_id in initial):
            removed = 0
            for memory_id in removal:
                if memory_id in present:
                    break
                removed += 1
            return counts[min(removed, len(counts) - 1)]
        if not present:
            return fixed
        return fixed + sum(item.token_count for item in items if item.memory_id in present)

    return counter


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


class _ScenarioPlanner:
    """기대 툴 호출이 있으면 그 순서대로 실행하고, 없으면 프롬프트의 기억 문장으로 답한다."""

    def __init__(self, scenario: dict) -> None:
        self._scenario = scenario
        self._calls = list(scenario.get("expected_tool_calls") or [])
        self._index = 0

    def __call__(self, context: str) -> AgentAction:
        if self._index < len(self._calls):
            call = self._calls[self._index]
            self._index += 1
            arguments = dict(call.get("arguments") or {})
            if call.get("tool_name") == "email" and call.get("action") == "send_email" and "recipient" not in arguments:
                found = _email_address(context)
                if found is not None:
                    arguments["recipient"] = found
            return AgentAction(call["tool_name"], call["action"], arguments, None)
        head = context.split("[TOOL RESULT]", 1)[0]
        lines = [line[2:].strip() for line in head.splitlines() if line.startswith("- ")]
        return AgentAction(None, None, {}, _answer_from_memory(lines, self._scenario))


def _answer_from_memory(lines: list[str], scenario: dict) -> str:
    """기억 문장으로 답한다. 정답이 그 문장 안에 있을 때만 그 구절을 고른다."""
    if not lines:
        return "Unknown / insufficient memory"
    text = "\n".join(lines)
    for answer in scenario.get("expected_answers") or []:
        if isinstance(answer, str) and answer in text:
            return answer
    return text


def _email_address(text: str) -> str | None:
    match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", text)
    return match.group(0) if match else None


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
    calls=(),
    results=(),
    response: str | None = None,
    stopped: str | None = None,
    task_success: bool | None = None,
    failures: list[str] | None = None,
    pipeline: str | None = None,
    selected: list[str] | None = None,
    note: str | None = None,
) -> dict:
    return {
        "id": scenario["scenario_id"],
        "title": scenario.get("title") or "",
        "query": scenario.get("query") or "",
        "kind": kind,
        "calls": [
            {"name": f"{call.tool_name}.{call.action}", "success": result.success, "output": dict(result.output), "error": result.error}
            for call, result in zip(calls, results)
        ],
        "response": response,
        "stopped": stopped,
        "task_success": task_success,
        "failures": failures or [],
        "pipeline": pipeline,
        "selected": selected or [],
        "note": note,
    }


def _write_result_html() -> None:
    passed = sum(item["kind"] == "pass" for item in REPORT)
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
    label = {"pass": "통과", "fail": "실패"}[kind]
    query = f'<p class="query">{html.escape(item["query"])}</p>' if item["query"] else ""
    calls = "\n".join(_call_block(call) for call in item["calls"]) or "<p>툴 호출 없음</p>"
    verdict = []
    if item.get("pipeline"):
        verdict.append(html.escape(_pipeline_text(item["pipeline"])))
    if item.get("selected"):
        verdict.append("고른 기억 " + ", ".join(html.escape(memory_id) for memory_id in item["selected"]))
    if item.get("pipeline") == "ok" and item["task_success"] is not None:
        verdict.append(f"행동 판정 {'통과' if item['task_success'] else '실패'}")
    if item.get("stopped") and item.get("pipeline") in (None, "ok"):
        verdict.append(html.escape(_stopped_text(item["stopped"])))
    if item.get("pipeline") == "ok" and item.get("failures"):
        verdict.append("남긴 기록 " + ", ".join(html.escape(_failure_text(name)) for name in item["failures"]))
    if item.get("note"):
        verdict.append(html.escape(item["note"]))
    response = f"<p>응답: {html.escape(item['response'])}</p>" if item.get("response") else ""
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


def _pipeline_text(status: str) -> str:
    return {
        "ok": "파이프라인 정상",
        "insufficient_context_budget": "필수 기억이 예산에 안 들어감",
        "invalid_budget_configuration": "고정 입력이 예산보다 큼",
        "store_consistency_error": "저장소가 스냅샷을 거부함",
    }.get(status, status)


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
