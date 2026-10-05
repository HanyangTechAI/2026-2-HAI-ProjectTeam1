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

import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.agent import Agent, AgentAction, usage_dict
from agent.context import provisional_token_count
from agent.evaluation import scenario_with_granted_approvals
from agent.tools import CalendarTool, EmailTool, FileTool, TaskTool, ToolCall, ToolExecutor
from benchmark.evaluator import evaluate
from memory.selector import PipelineStatus
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
    if not args:
        _run_skeleton()
        _run_scenarios(scenarios, ["S13"])
        print("demo ok")
        return
    if args == ["--all"]:
        _run_scenarios(scenarios, [item["scenario_id"] for item in scenarios])
        print("demo ok")
        return
    _run_scenarios(scenarios, args)
    print("demo ok")


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
    _check_scenario_tool_shapes()


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


def _print_trace(title, response, calls, results) -> None:
    print(title)
    for call, result in zip(calls, results):
        detail = f"output={result.output}" if result.success else f"error={result.error}"
        print(f"  {call.tool_name}.{call.action} -> success={result.success} {detail}")
    print(f"  response: {response}")


if __name__ == "__main__":
    main(sys.argv[1:])
