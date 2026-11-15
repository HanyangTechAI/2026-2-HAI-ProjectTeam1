from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence

from agent.agent import Agent, AgentAction
from context.tokenizer import (
    TokenCounter,
    provisional_token_count,
)
from memory.criticality_policy import make_criticality_policy
from memory.experimental_packing import validate_alpha
from memory.retrieval import Embedder
from memory.schema import MemoryItem
from memory.selector import PipelineStatus, SelectionResult
from memory.task_criticality import (
    CriticalPackingResult,
    EvidenceStatus,
    _assess_memory,
    _EMAIL,
    _FILE,
)
from pipeline.orchestrator import (
    GoalChecker,
    PipelineRunResult,
)
from pipeline.pipeline_adapter import PipelineAdapter


@dataclass(frozen=True)
class GateEvent:
    outcome: str
    reason: str
    action: str | None
    slot: str | None = None


@dataclass(frozen=True)
class GatedRun:
    pipeline: PipelineRunResult
    gate_events: tuple[GateEvent, ...]


class _RunGate:
    """실행마다 독립적으로 생성되는 Gate 상태."""

    def __init__(self) -> None:
        self.decision: CriticalPackingResult | None = None
        self.selection: SelectionResult | None = None
        self.query: str | None = None
        self.events: list[GateEvent] = []

    def reset_step(self) -> None:
        self.decision = None
        self.selection = None
        self.query = None

    def on_decision(
        self,
        decision: CriticalPackingResult,
    ) -> None:
        if self.decision is not None:
            raise RuntimeError(
                "multiple decisions for one selection step"
            )
        self.decision = decision

    def bind(
        self,
        selection: SelectionResult,
        query: str,
    ) -> None:
        if (
            selection.status != PipelineStatus.OK
            or self.decision is None
        ):
            raise RuntimeError(
                "missing criticality decision on successful selection"
            )

        self.selection = selection
        self.query = query

    def _visible_values(
        self,
        slot: str,
        context: str,
    ) -> set[str]:
        """최종 Context에 실제 존재하는 값만 반환.

        선택됐지만 Context Builder가 삭제한 Memory는
        Tool 인자의 근거로 인정하지 않는다.
        """
        if self.selection is None or self.query is None:
            return set()

        values: set[str] = set()

        if slot in ("filename", "attachment"):
            values.update(
                value.casefold()
                for value in _FILE.findall(self.query)
            )

        all_memories = (
            *self.selection.protected,
            *self.selection.states,
            *self.selection.flexible,
        )

        for memory in all_memories:
            if "- " + memory.content.strip() not in context:
                continue

            evidence, invalid_reason = _assess_memory(
                memory,
                query=self.query,
                slots=(slot,),
            )

            if invalid_reason is None:
                values.update(
                    item.value.casefold()
                    for item in evidence
                )

        return values

    def _event(
        self,
        outcome: str,
        reason: str,
        action: str | None,
        slot: str | None = None,
    ) -> GateEvent:
        event = GateEvent(
            outcome=outcome,
            reason=reason,
            action=action,
            slot=slot,
        )
        self.events.append(event)
        return event

    def check(
        self,
        action: AgentAction,
        context: str,
    ) -> GateEvent:
        if action.tool_name is None and action.action is None:
            return self._event(
                "no_tool",
                "planner_abstained",
                None,
            )

        actual = f"{action.tool_name}.{action.action}"

        if (
            self.decision is None
            or self.selection is None
            or self.query is None
        ):
            return self._event(
                "blocked",
                "missing_gate_state",
                actual,
            )

        target = self.decision.action

        if target is None:
            if (
                action.tool_name == "file"
                or actual == "email.send_email"
            ):
                return self._event(
                    "blocked",
                    "unsupported_sensitive_intent",
                    actual,
                )

            return self._event(
                "passthrough",
                "unsupported_intent",
                actual,
            )

        if (
            target == "email.send_email"
            and actual == "email.request_approval"
        ):
            recipient = action.arguments.get("recipient")
            recipients = {
                value.casefold()
                for value in _EMAIL.findall(self.query)
            }

            if (
                len(recipients) != 1
                or not isinstance(recipient, str)
                or recipient.casefold() not in recipients
            ):
                return self._event(
                    "blocked",
                    "recipient_not_grounded",
                    actual,
                    "recipient",
                )

            attachment = action.arguments.get("attachment")

            if attachment is not None and attachment != "":
                allowed = self._visible_values(
                    "attachment",
                    context,
                )

                if (
                    len(allowed) != 1
                    or not isinstance(attachment, str)
                    or attachment.casefold() not in allowed
                ):
                    return self._event(
                        "blocked",
                        "attachment_not_grounded",
                        actual,
                        "attachment",
                    )

            return self._event(
                "allowed",
                "safe_approval_request",
                actual,
            )

        if actual != target:
            return self._event(
                "blocked",
                "unexpected_tool_action",
                actual,
            )

        statuses = dict(self.decision.slot_statuses)

        for slot in self.decision.required_slots:
            if (
                statuses.get(slot)
                != EvidenceStatus.VALID_CANDIDATE.value
            ):
                return self._event(
                    "blocked",
                    "slot_not_valid_candidate",
                    actual,
                    slot,
                )

            values = self._visible_values(
                slot,
                context,
            )

            if len(values) != 1:
                return self._event(
                    "blocked",
                    "final_context_evidence_missing_or_conflicting",
                    actual,
                    slot,
                )

            argument = action.arguments.get(slot)

            if (
                not isinstance(argument, str)
                or argument.casefold() not in values
            ):
                return self._event(
                    "blocked",
                    "tool_argument_not_grounded",
                    actual,
                    slot,
                )

        if target == "email.send_email":
            recipients = {
                value.casefold()
                for value in _EMAIL.findall(self.query)
            }

            recipient = action.arguments.get("recipient")

            if (
                len(recipients) != 1
                or not isinstance(recipient, str)
                or recipient.casefold() not in recipients
            ):
                return self._event(
                    "blocked",
                    "recipient_not_grounded",
                    actual,
                    "recipient",
                )

        return self._event(
            "allowed",
            "evidence_and_arguments_verified",
            actual,
        )


class _GatePlanner:
    """Frozen Agent가 ToolExecutor를 호출하기 전에 행동을 검사."""

    def __init__(
        self,
        original,
        gate: _RunGate,
    ) -> None:
        self._original = original
        self._gate = gate

    def plan(self, context: str) -> AgentAction:
        action = self._original.plan(context)

        if not isinstance(action, AgentAction):
            raise TypeError(
                "planner must return AgentAction"
            )

        verdict = self._gate.check(
            action,
            context,
        )

        if verdict.outcome != "blocked":
            return action

        return AgentAction(
            tool_name=None,
            action=None,
            arguments={},
            text_response=(
                "안전 보류: 필요한 정보가 확인되지 않았거나 "
                "충돌합니다. "
                f"({verdict.reason}, "
                f"slot={verdict.slot or '-'})"
            ),
        )

    def __getattr__(self, name: str):
        return getattr(self._original, name)


class CriticalityGatedAdapter:
    """기존 PipelineAdapter에 Run-local Safety Gate를 연결."""

    def __init__(
        self,
        *,
        system_prompt: str,
        tool_definitions: str,
        embedder: Embedder,
        agent_factory: Callable[[], Agent],
        token_counter: TokenCounter = provisional_token_count,
        top_k: int = 5,
        utility_weights: tuple[float, float, float] = (
            0.6, 0.3, 0.1
        ),
        fallback_alpha: float = 1.0,
        max_steps: int = 1,
        goal_checker: GoalChecker | None = None,
    ) -> None:
        validate_alpha(fallback_alpha)

        if max_steps != 1:
            raise ValueError(
                "v0.3 supports max_steps=1 only"
            )

        if (
            not callable(agent_factory)
            or not callable(embedder)
        ):
            raise TypeError(
                "agent_factory and embedder must be callable"
            )

        self.system_prompt = system_prompt
        self.tool_definitions = tool_definitions
        self.embedder = embedder
        self.agent_factory = agent_factory
        self.token_counter = token_counter
        self.top_k = top_k
        self.utility_weights = utility_weights
        self.fallback_alpha = fallback_alpha
        self.goal_checker = goal_checker

    def run_with_audit(
        self,
        *,
        query: str,
        memories: Sequence[MemoryItem],
        session_id: str,
        total_budget: int,
        current_turn: int,
        current_time: datetime | None = None,
    ) -> GatedRun:
        gate = _RunGate()

        critical_policy = make_criticality_policy(
            embedder=self.embedder,
            top_k=self.top_k,
            utility_weights=self.utility_weights,
            fallback_alpha=self.fallback_alpha,
            on_decision=gate.on_decision,
        )

        def tracking_policy(**kwargs) -> SelectionResult:
            gate.reset_step()

            selected = critical_policy(**kwargs)

            if selected.status == PipelineStatus.OK:
                gate.bind(
                    selected,
                    kwargs["query"],
                )

            return selected

        def guarded_agent_factory() -> Agent:
            agent = self.agent_factory()

            if type(agent) is not Agent:
                raise TypeError(
                    "v0.3 requires an unmodified Agent instance"
                )

            agent.planner = _GatePlanner(
                agent.planner,
                gate,
            )

            old_checker = agent.goal_checker

            if old_checker is not None:
                agent.goal_checker = (
                    lambda response, calls, results:
                    bool(calls)
                    and bool(results)
                    and bool(
                        old_checker(
                            response,
                            calls,
                            results,
                        )
                    )
                )

            return agent

        def safe_checker(
            response,
            calls,
            results,
        ) -> bool:
            if (
                not calls
                or not results
                or self.goal_checker is None
            ):
                return False

            return bool(
                self.goal_checker(
                    response,
                    calls,
                    results,
                )
            )

        adapter = PipelineAdapter(
            policy=tracking_policy,
            agent_factory=guarded_agent_factory,
            system_prompt=self.system_prompt,
            tool_definitions=self.tool_definitions,
            token_counter=self.token_counter,
            max_steps=1,
            goal_checker=(
                safe_checker
                if self.goal_checker is not None
                else None
            ),
        )

        pipeline = adapter(
            query=query,
            memories=memories,
            session_id=session_id,
            total_budget=total_budget,
            current_turn=current_turn,
            current_time=current_time,
        )

        return GatedRun(
            pipeline=pipeline,
            gate_events=tuple(gate.events),
        )

    def __call__(self, **kwargs) -> PipelineRunResult:
        return self.run_with_audit(**kwargs).pipeline
