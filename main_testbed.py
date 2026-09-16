"""120-turn simulator for Constraint-Preserving Budget-Aware Memory.

Scenario
--------
Turn 3   : hard constraint stored as PROTECTED memory.
Turn 15  : report filename v1 stored as CURRENT_STATE.
Turn 60  : v1 marked SUPERSEDED; v2 (report_final.pdf) becomes ACTIVE.
Turn 120 : user asks to email the external report; the agent acts from
           budget-selected context, then the evaluator scores the run.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Optional

from agent import ConstraintAwareAgent, run_tool_loop
from budget_manager import BudgetAwareContextBuilder
from evaluator import ExperimentEvaluator
from memory_schema import MemoryRecord, MemoryType, ValidStatus
from mock_environment import (
    CONSTRAINT_TEXT,
    MockEnvironment,
    STATE_V1_TEXT,
    STATE_V2_TEXT,
)
from report import build_view, render_terminal, write_html

# Tight enough that unranked flexible chatter cannot crowd out C_P items,
# yet large enough to admit a few high-utility flexible memories.
DEFAULT_TOKEN_BUDGET = 400


def run_simulation(total_budget: int = DEFAULT_TOKEN_BUDGET) -> dict:
    """Execute the 120-turn scenario and return evaluator metrics."""

    memories: List[MemoryRecord] = []
    history_logs: List[dict] = []
    env = MockEnvironment()
    agent = ConstraintAwareAgent()
    builder = BudgetAwareContextBuilder(total_budget=total_budget)

    final_context = ""
    final_stats: dict = {}

    for turn in range(1, env.TOTAL_TURNS + 1):
        event = env.observe(turn)
        _maybe_write_scenario_memory(turn, memories)
        memories.extend(_filler_flexible_memories(turn))

        query = event.user_message or _probe_query(turn)
        context, stats = builder.build_context(memories, query)

        log = _log_turn(turn, context, stats, memories)
        history_logs.append(log)

        if event.is_task_turn and event.user_message:
            final_context, final_stats = context, stats
            run_tool_loop(agent, env, context, event.user_message)
            log["is_task_turn"] = True
            log["task_success"] = env.task_succeeded()
            log["agent_tool_trace"] = list(env.tool_trace)

    metrics = ExperimentEvaluator().evaluate_run(history_logs, env)
    return {
        "metrics": metrics,
        "context_stats": final_stats,
        "final_context": final_context,
        "tool_trace": list(env.tool_trace),
        "emails": list(env.sent_emails),
        "task_success": env.task_succeeded(),
        "constraint_text": CONSTRAINT_TEXT,
    }


def _maybe_write_scenario_memory(
    turn: int,
    memories: List[MemoryRecord],
) -> None:
    """Store scripted events as memories. World-state updates live in the env."""

    if turn == 3:
        memories.append(
            MemoryRecord.create(
                memory_type=MemoryType.PROTECTED,
                content=CONSTRAINT_TEXT,
                source_turn=turn,
                scope="email_send",
            )
        )
        return

    if turn == 15:
        memories.append(
            MemoryRecord.create(
                memory_type=MemoryType.CURRENT_STATE,
                content=STATE_V1_TEXT,
                source_turn=turn,
                scope="file:report",
                version=1,
            )
        )
        return

    if turn == 60:
        for memory in memories:
            if (
                memory.memory_type == MemoryType.CURRENT_STATE
                and memory.scope == "file:report"
                and memory.valid_status == ValidStatus.ACTIVE
            ):
                memory.valid_status = ValidStatus.SUPERSEDED
        memories.append(
            MemoryRecord.create(
                memory_type=MemoryType.CURRENT_STATE,
                content=STATE_V2_TEXT,
                source_turn=turn,
                scope="file:report",
                version=2,
            )
        )


def _filler_flexible_memories(turn: int) -> List[MemoryRecord]:
    """Inject distractor FLEXIBLE memories so B_flex selection is non-trivial.

    Several fillers deliberately mention ``report_v1.pdf`` after the state
    update, testing that superseded facts are not treated as current state
    and that utility ranking prefers query-relevant notes.
    """

    fillers: List[MemoryRecord] = []

    # Recurring low-value chatter that should lose the B_flex packing contest.
    if turn % 7 == 0:
        fillers.append(
            MemoryRecord.create(
                memory_type=MemoryType.FLEXIBLE,
                content=f"Turn {turn} standup: weather is fine, no blockers.",
                source_turn=turn,
                scope="global",
            )
        )

    # Query-relevant but potentially stale filename mention (distractor).
    if turn in {25, 45, 75, 90, 110}:
        fillers.append(
            MemoryRecord.create(
                memory_type=MemoryType.FLEXIBLE,
                content=(
                    "Old working note: draft attachment might still be "
                    "report_v1.pdf - verify before sending."
                ),
                source_turn=turn,
                scope="file:report",
            )
        )

    if turn in {40, 80, 100}:
        fillers.append(
            MemoryRecord.create(
                memory_type=MemoryType.FLEXIBLE,
                content=(
                    "Email draft fragments for the external partner report "
                    "delivery; body should summarize the latest findings."
                ),
                source_turn=turn,
                scope="email_send",
            )
        )

    return fillers


def _probe_query(turn: int) -> str:
    """Per-turn retrieval query used to exercise budgeted context building."""

    if turn >= 60:
        return "최신 보고서 파일명과 외부 이메일 승인 규칙을 확인해줘."
    if turn >= 15:
        return "현재 보고서 파일 상태와 이메일 제약 조건을 상기해줘."
    if turn >= 3:
        return "외부 이메일을 보낼 때 지켜야 할 규칙은 무엇인가?"
    return "오늘 작업 메모를 정리해줘."


def _log_turn(
    turn: int,
    context: str,
    stats: dict,
    memories: List[MemoryRecord],
) -> dict:
    """Record whether protected / active-state memories survived budgeting."""

    has_protected = any(
        m.memory_type == MemoryType.PROTECTED and m.valid_status == ValidStatus.ACTIVE
        for m in memories
    )
    active_state = _active_current_state(memories)

    protected_in_context = CONSTRAINT_TEXT in context
    state_in_context = (
        active_state is not None and active_state.content in context
    )
    stale_treated_as_active = (
        turn >= 60
        and STATE_V1_TEXT in context
        and "[CURRENT STATE]" in context
        and _stale_listed_under_current_state(context)
    )

    constraint_relevant = turn >= 3 and has_protected
    state_relevant = active_state is not None

    return {
        "turn": turn,
        "is_task_turn": False,
        "task_success": False,
        "constraint_relevant": constraint_relevant,
        "constraint_violated": constraint_relevant and not protected_in_context,
        "state_relevant": state_relevant,
        "used_current_state": state_relevant
        and state_in_context
        and not stale_treated_as_active,
        "c_p_tokens": stats["c_p_tokens"],
        "b_flex_tokens": stats["b_flex_tokens"],
        "used_tokens": stats["used_tokens"],
        "selected_count": stats["selected_count"],
    }


def _active_current_state(memories: List[MemoryRecord]) -> Optional[MemoryRecord]:
    for memory in memories:
        if (
            memory.memory_type == MemoryType.CURRENT_STATE
            and memory.valid_status == ValidStatus.ACTIVE
            and memory.scope == "file:report"
        ):
            return memory
    return None


def _stale_listed_under_current_state(context: str) -> bool:
    state_section = _section_body(context, "[CURRENT STATE]", "[FLEXIBLE MEMORY]")
    return STATE_V1_TEXT in state_section


def _section_body(context: str, start: str, end: str) -> str:
    match = re.search(re.escape(start) + r"(.*?)" + re.escape(end), context, re.S)
    return match.group(1) if match else ""


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    result = run_simulation()
    if "--json" in sys.argv:
        print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
        return

    view = build_view(result)
    print(render_terminal(view))
    html_path = write_html(view, Path(__file__).with_name("run_report.html"))
    print(f"HTML 리포트: {html_path}")


if __name__ == "__main__":
    main()
