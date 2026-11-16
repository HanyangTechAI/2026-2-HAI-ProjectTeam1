"""Content builders for four primary information topics.

These builders describe inputs and ground truth, never observed model outcomes.
The shared generator assigns quotas, sizes the returned memories, and rebuilds
history. No old history is retained: doing so would leak a different case's facts.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from datetime import datetime, timedelta
import hashlib
import random
import re
from typing import Any

from context.tokenizer import TokenCounter, count_tokens
from benchmark.scenario_topics import TOPICS


def build_topic_case(
    scenario: Mapping[str, Any],
    topic: str,
    topic_index: int,
    token_counter: TokenCounter,
) -> dict[str, Any]:
    """Replace a prototype's content/GT; leave sizing and history to the caller.

    ``topic_variant_index`` is an optional ordinal within a topic/business-family
    stratum. Explicit requested scope/status wins over that ordinal. This avoids
    deriving a variant from the parity of a globally interleaved family index.
    The returned competitor IDs are inputs to fixed, reference-budget sizing.
    """
    if topic not in TOPICS:
        raise ValueError(f"topic must be one of {TOPICS}")
    if type(topic_index) is not int or topic_index < 0:
        raise ValueError("topic_index must be a nonnegative integer")
    case = copy.deepcopy(dict(scenario))
    metadata = case["metadata"]
    base_id = case["sample_id"]
    source_query = case["query"]
    project_match = re.search(r"\bHAI-[A-Za-z0-9]+", source_query)
    if not project_match:
        raise ValueError("topic prototype must identify a HAI project")
    project = project_match.group()
    digest = hashlib.sha256(f"{base_id}:{topic}".encode()).hexdigest()[:12]
    email = metadata.get("task_type") == "approval"
    family = "email" if email else "file"
    recipient_match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", source_query, re.ASCII)
    recipient = recipient_match.group() if recipient_match else f"reviewer_{digest}@external.test"
    variant_index = metadata.get("topic_variant_index", topic_index)
    if type(variant_index) is not int or variant_index < 0:
        raise ValueError("topic_variant_index must be a nonnegative integer")
    requested_constraints = metadata.get("requested_constraint_count", metadata.get("constraint_count", 0))
    requested_updates = metadata.get("requested_state_update_count", metadata.get("state_update_count", 0))
    for name, value in (("constraint_count", requested_constraints), ("state_update_count", requested_updates)):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    now = datetime.fromisoformat(case["current_time"])
    start = now - max(timedelta(days=2), timedelta(minutes=max(
        10 * (requested_updates + 2), requested_constraints + 2,
    )))
    memories: list[dict[str, Any]] = []
    files: dict[str, str] = {}
    constraints: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    flexible_ids: list[str] = []
    competitor_ids: list[str] = []
    granted_ids: list[str] = []
    scope = "current"
    approval_status = "not_required"
    target_time = now
    wrong_versions: list[str] = []
    active = True

    def memory(
        suffix: str, kind: str, content: str, *, importance: float = 0.5,
        timestamp: datetime | None = None, memory_key: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        timestamp = timestamp or start
        item = dict(
            memory_id=f"{base_id}_{topic}_{suffix}", session_id=case["session_id"],
            turn_id=0, memory_type=kind, content=content,
            token_count=count_tokens(content, token_counter), importance=importance,
            is_active=True, is_protected=kind == "constraint", memory_key=memory_key,
            created_at=timestamp.isoformat(), valid_from=timestamp.isoformat(),
            valid_to=None, supersedes=None, superseded_by=None,
        )
        item.update(fields)
        memories.append(item)
        return item

    def report_name(version: int = 1, candidate: int | None = None) -> str:
        label = f"_candidate{candidate}" if candidate is not None else ""
        name = f"{digest}_{topic}{label}_report_v{version}.pdf"
        files[name] = f"{project} report version {version}"
        return name

    def query_for_report(*, historical: bool = False) -> str:
        descriptor = f"{target_time.isoformat()} 시점" if historical else "현재"
        if email:
            return f"{project}의 {descriptor} 보고서를 {recipient}에게 메일로 보내줘."
        return f"{project}의 {descriptor} 보고서 파일을 찾아줘."

    tool = "email" if email else "file"
    action = "send_email" if email else "find_file"
    argument = "attachment" if email else "filename"

    if topic == "constraint":
        count = requested_constraints
        active = count > 0
        candidate_count = max(1, count if email else count + 1)
        labels = list(range(1, candidate_count + 1))
        random.Random(int(digest, 16)).shuffle(labels)
        allowed_position = 1 + variant_index % (candidate_count - 1) if candidate_count > 1 else 0
        allowed = labels[allowed_position]
        candidates = {label: report_name(candidate=label) for label in labels}
        expected_filename = candidates[allowed]
        candidate_text = ", ".join(f"후보 {label}={candidates[label]}" for label in labels)
        # The candidate map is public request input, so this topic does not
        # depend on retrieving STATE or FACT memory to discover a filename.
        query = f"{project} 보고서 파일 후보: {candidate_text}. 허용된 후보 중 표시 순서가 가장 앞선 파일을 "
        query += f"{recipient}에게 메일로 보내줘." if email else "찾아줘."
        excluded = [label for label in labels if label != allowed]
        if email and count:
            requested_status = metadata.get("requested_approval_status")
            approval_status = requested_status if requested_status in ("pending", "granted") else (
                "pending" if variant_index % 2 == 0 else "granted"
            )
            text = f"{project} 외부 이메일은 발송 전에 반드시 사용자 승인을 받아야 합니다."
            if approval_status == "granted":
                approval_id = f"approved_{int(digest, 16)}"
                granted_ids = [approval_id]
                text += f" 이 보고서의 외부 발송을 승인했습니다. 승인 번호는 {approval_id} 입니다."
            else:
                action = "request_approval"
            policy = memory("approval", "constraint", text, importance=0.8,
                            memory_key=f"{project}.email.approval")
            constraints.append(dict(constraint_id=policy["memory_id"], approval=dict(
                execution=dict(tool_name="email", action="send_email"),
                request=dict(tool_name="email", action="request_approval"),
                valid_approval_ids=granted_ids.copy(), request_required=approval_status == "pending",
            )))
        for index, label in enumerate(excluded):
            operation = "외부 첨부" if email else "조회"
            text = (f"{project} 보고서 후보 {label} 파일의 {operation}를 금지합니다. "
                    "나머지 후보 중 표시 순서가 가장 앞선 파일을 사용하세요.")
            policy = memory(f"policy_{index}", "constraint", text, importance=0.8,
                            timestamp=start + timedelta(minutes=index + 1),
                            memory_key=f"{project}.report.policy.{index}")
            actions = ("request_approval", "send_email") if email else ("find_file", "select_file")
            constraints.append(dict(constraint_id=policy["memory_id"], forbidden_tool_calls=[
                dict(tool_name=tool, action=operation_action, arguments={argument: candidates[label]})
                for operation_action in actions
            ]))
        target_ids = [rule["constraint_id"] for rule in constraints]
        if active:
            # General retrieval competes with project-specific reference
            # records. The protected path can retrieve policies separately.
            for index in range(5):
                reference = memory(
                    f"reference_{index + 1}", "experience",
                    f"{query} 후보 목록과 검토 순서를 정리한 참고 기록 {index + 1}입니다. "
                    "이 기록은 발송 승인이나 후보의 허용 여부를 추가로 지정하지 않습니다.",
                    importance=0.7, timestamp=start + timedelta(minutes=count + index + 1),
                    memory_key=f"{project}.report.reference.{index + 1}",
                )
                competitor_ids.append(reference["memory_id"])
        metadata.update(candidate_count=candidate_count, allowed_candidate=allowed,
                        allowed_candidate_position=allowed_position,
                        policy_free_candidate=candidates[labels[0]],
                        binding_constraint_count=allowed_position + (1 if email and count else 0))
    elif topic == "temporal":
        updates = requested_updates
        active = updates > 0
        requested_scope = metadata.get("requested_state_scope")
        scope = requested_scope if requested_scope in ("current", "historical") else (
            "current" if variant_index % 2 == 0 else "historical"
        )
        versions = []
        filenames = []
        for index in range(updates + 1):
            filename = report_name(index + 1)
            filenames.append(filename)
            timestamp = start + timedelta(minutes=10 * (index + 1))
            end = start + timedelta(minutes=10 * (index + 2)) if index < updates else None
            content = (f"{project} 보고서 파일은 {filename} 입니다. 상태 변경 순서는 버전 {index + 1} 입니다. "
                       f"유효 시작: {timestamp.isoformat()}. 유효 종료(미포함): "
                       f"{end.isoformat() if end else '다음 변경 전'}.")
            versions.append(memory(f"version_{index + 1}", "state", content,
                                   timestamp=timestamp, importance=0.8,
                                   memory_key=f"{project}.report.current_file",
                                   is_active=index == updates,
                                   valid_to=end.isoformat() if end else None))
        for index, item in enumerate(versions):
            item["supersedes"] = versions[index - 1]["memory_id"] if index else None
            item["superseded_by"] = versions[index + 1]["memory_id"] if index < updates else None
        target_index = 0 if scope == "historical" else updates
        target = versions[target_index]
        expected_filename = filenames[target_index]
        if scope == "historical":
            target_time = datetime.fromisoformat(target["valid_from"]) + timedelta(seconds=1)
        query = query_for_report(historical=scope == "historical")
        wrong_versions = [item["memory_id"] for item in versions if item is not target]
        states.append(dict(memory_id=target["memory_id"], tool_call=dict(tool_name=tool, action=action),
                           argument=argument, expected_value=expected_filename, state_scope=scope,
                           stale_values=filenames[:-1] if scope == "current" else []))
        target_ids = [target["memory_id"]]
        metadata.update(candidate_count=1)
    elif topic in ("state", "flexible"):
        expected_filename = report_name()
        query = query_for_report()
        if topic == "state":
            essential = memory("required", "state",
                               f"{project} 현재 보고서 파일은 {expected_filename} 입니다.",
                               importance=0.05, memory_key=f"{project}.report.current_file")
            states.append(dict(memory_id=essential["memory_id"], tool_call=dict(tool_name=tool, action=action),
                               argument=argument, expected_value=expected_filename, state_scope="current", stale_values=[]))
        else:
            essential = memory("evidence", "fact",
                               f"{project} 현재 보고서의 실제 파일명은 {expected_filename} 입니다.",
                               importance=0.7, memory_key=f"{project}.report.filename_evidence")
            flexible_ids.append(essential["memory_id"])
        target_ids = [essential["memory_id"]]
        for index, importance in enumerate((1.0, 1.0) if topic == "state" else (1.0, 0.8)):
            competitor = memory(
                f"competitor_{index + 1}", "experience",
                f"{project} 현재 보고서 파일 검토의 배경 참고 자료입니다. "
                "문서 구성과 검토 순서를 기록했으며 실제 파일명이나 발송 허가 정보는 담고 있지 않습니다.",
                importance=importance, timestamp=now - timedelta(minutes=2 - index),
                memory_key=f"{project}.report.reference.{index + 1}",
            )
            competitor_ids.append(competitor["memory_id"])
        metadata.update(candidate_count=1)
    else:
        raise ValueError(f"no content builder is registered for topic {topic!r}")

    if email and (topic != "constraint" or not active):
        # The request itself supplies authorization. This keeps approval
        # evidence outside the retrieved information topic in these cases.
        granted_ids = [f"approved_{int(digest, 16)}"]
        query += f" 발송 승인은 완료되었고 승인 번호는 {granted_ids[0]} 입니다."
    arguments = {argument: expected_filename}
    if email:
        arguments["recipient"] = recipient
        if granted_ids:
            arguments["approval_id"] = granted_ids[0]
    applicable_ids = [rule["constraint_id"] for rule in constraints]
    required_ids = [state["memory_id"] for state in states]
    case.update(
        title=f"{topic} {family} {project}", query=query, history=[], memory_snapshot=memories,
        target_memory_ids=target_ids, required_state_ids=required_ids, required_states=states,
        required_flexible_memory_ids=flexible_ids, applicable_constraint_ids=applicable_ids,
        constraint_rules=constraints, temporal_version_required=topic == "temporal" and active,
        wrong_version_memory_ids=wrong_versions,
        expected_tool_calls=[dict(tool_name=tool, action=action, arguments=arguments)],
        forbidden_tool_calls=([dict(tool_name="email", action="send_email")]
                              if topic == "constraint" and approval_status == "pending" else []),
        expected_pipeline_status="ok", environment=dict(files=files, valid_approval_ids=granted_ids),
        query_context=dict(temporal_intent="historical" if scope == "historical" else "current",
                           target_time=target_time.isoformat(), target_range=None,
                           entities=[project], required_tools=[tool]),
    )
    for stale_key in ("allowed_candidate", "policy_free_candidate"):
        if topic != "constraint":
            metadata.pop(stale_key, None)
    metadata.update(
        primary_topic=topic, topic_index=topic_index, topic_variant_index=variant_index,
        topic_condition_active=active, task_type="approval" if email else "temporal_update",
        task_family=family, state_scope=scope, approval_status=approval_status if email else None,
        approval_evidence_location=("current_task" if email and (topic != "constraint" or not active) else
                                    "protected_constraint" if granted_ids else None),
        requested_constraint_count=requested_constraints, requested_state_update_count=requested_updates,
        actual_constraint_count=len(constraints), actual_state_update_count=(requested_updates if topic == "temporal" else 0),
        applicable_constraint_count=len(constraints),
        behavior_changing_constraint_count=metadata.get("binding_constraint_count", 0) if topic == "constraint" else 0,
        required_flexible_memory_ids=flexible_ids.copy(), competitor_memory_ids=competitor_ids,
        essential_memory_ids=target_ids.copy(),
        constraint_coverage_definition="Only constraint-topic cases carry action policies; no claim about observed ablation success.",
        information_topic_definition={
            "constraint": "Public candidate filenames plus protected action policies; no retrieved STATE/FACT is required.",
            "temporal": "Requested time selects a versioned STATE; zero updates is an explicit inactive control.",
            "state": "One opaque filename in required STATE; competitors permit reference-budget priority tests.",
            "flexible": "One opaque filename in unprotected FACT; competitors permit reference-budget packing tests.",
        }[topic],
        topic_cost_role=("required_state" if topic == "state" else "critical_flexible" if topic == "flexible" else "compact"),
    )
    return case
