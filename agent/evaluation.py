"""평가기가 읽는 시나리오 필드에 실행 중 발급된 승인 ID를 반영한다.

benchmark.evaluator는 ToolResult.approved를 보지 않는다.
send_email의 approval_id가 constraint_rules.approval.valid_approval_ids에
있을 때만 승인된 발송으로 센다. request_approval이 성공해 환경이 발급한 ID만
그 목록에 더한다. 요청에 실패했거나 발송이 다른 ID를 쓰면 목록에 없다.
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
    """시나리오 원본은 바꾸지 않고, 발급된 승인 ID를 더한 사본을 반환한다."""
    updated: dict[str, Any] = copy.deepcopy(dict(scenario))
    granted: list[str] = []
    for result in tool_results:
        if not result.success or result.tool_name != "email" or result.action != "request_approval":
            continue
        approval_id = result.output.get("approval_id")
        if isinstance(approval_id, str) and approval_id not in granted:
            granted.append(approval_id)

    for rule in updated.get("constraint_rules", []):
        if not isinstance(rule, dict):
            continue
        approval = rule.get("approval")
        if not isinstance(approval, dict):
            continue
        current = [
            item for item in approval.get("valid_approval_ids", [])
            if isinstance(item, str)
        ]
        approval["valid_approval_ids"] = current + [
            item for item in granted if item not in current
        ]
    return updated
