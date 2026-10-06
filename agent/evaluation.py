"""평가기가 읽는 시나리오 필드에서 pending 승인 ID를 빼 둔다.

benchmark.evaluator는 ToolResult.approved를 보지 않는다.
send_email의 approval_id가 constraint_rules.approval.valid_approval_ids에
있을 때만 승인된 발송으로 센다. request_approval이 돌려준 pending ID는
아직 승인이 아니므로 그 목록에 넣지 않고, 이미 들어 있으면 뺀다.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from .tools.types import ToolResult


def scenario_with_granted_approvals(
    scenario: Mapping[str, Any],
    tool_results: Sequence[ToolResult],
) -> dict[str, Any]:
    """시나리오 원본은 바꾸지 않고, pending 승인 ID를 뺀 사본을 반환한다."""
    pending = {
        result.output.get("approval_id")
        for result in tool_results
        if result.success
        and result.tool_name == "email"
        and result.action == "request_approval"
        and result.output.get("status") == "pending"
        and isinstance(result.output.get("approval_id"), str)
    }
    updated: dict[str, Any] = copy.deepcopy(dict(scenario))
    for rule in updated.get("constraint_rules", []):
        if not isinstance(rule, dict):
            continue
        approval = rule.get("approval")
        if not isinstance(approval, dict):
            continue
        approval["valid_approval_ids"] = [
            item
            for item in approval.get("valid_approval_ids", [])
            if isinstance(item, str) and item not in pending
        ]
    return updated
