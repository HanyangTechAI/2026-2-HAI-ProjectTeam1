"""Context-grounded agent for the long-horizon memory testbed.

The agent never reads environment ground truth (active filenames, approval
flags). It only sees:
    1. the budgeted memory context string
    2. the current user message
    3. public tool schemas
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Sequence

from mock_environment import (
    EMAIL_SUBJECT,
    EXTERNAL_RECIPIENT,
    MockEnvironment,
    ToolResult,
    ToolSpec,
)


@dataclass
class ToolCall:
    """One planned environment action."""

    name: str
    arguments: Dict[str, str]


class BaseAgent(ABC):
    """Agent interface used by the experiment loop."""

    @abstractmethod
    def act(
        self,
        context: str,
        user_message: str,
        tools: Sequence[ToolSpec],
    ) -> List[ToolCall]:
        """Return an ordered tool plan for the current observation."""


class ConstraintAwareAgent(BaseAgent):
    """Follows PROTECTED constraints and ACTIVE current-state from context.

    If the budget builder dropped protected memory, this agent will skip
    approval and the environment will record a constraint violation.
    If only a stale filename is visible under [CURRENT STATE], it will
    attach that file and the environment will record OUTDATED_STATE_USED.
    """

    def act(
        self,
        context: str,
        user_message: str,
        tools: Sequence[ToolSpec],
    ) -> List[ToolCall]:
        if not user_message or not self._is_send_email_task(user_message):
            return []

        tool_names = {spec.name for spec in tools}
        recipient = self._recipient_from_context(context) or EXTERNAL_RECIPIENT
        attachment = self._attachment_from_context(context)
        calls: List[ToolCall] = []

        if "request_approval" in tool_names and self._requires_approval(context):
            calls.append(
                ToolCall(
                    name="request_approval",
                    arguments={"recipient": recipient, "subject": EMAIL_SUBJECT},
                )
            )

        if "send_email" in tool_names:
            calls.append(
                ToolCall(
                    name="send_email",
                    arguments={
                        "recipient": recipient,
                        "body": "Attached is the external report as requested.",
                        "attachment": attachment,
                    },
                )
            )
        return calls

    @staticmethod
    def _is_send_email_task(user_message: str) -> bool:
        lowered = user_message.lower()
        return "이메일" in user_message or "email" in lowered or "보내" in user_message

    @staticmethod
    def _requires_approval(context: str) -> bool:
        """Honor the constraint only if it actually survived into the prompt."""

        protected = _section_body(context, "[PROTECTED MEMORY]", "[CURRENT STATE]").strip()
        if not protected or protected == "(none)":
            return False
        return "request_approval" in protected or "승인" in protected

    @staticmethod
    def _attachment_from_context(context: str) -> str:
        """Prefer the ACTIVE current-state filename; never use env oracle."""

        state_section = _section_body(context, "[CURRENT STATE]", "[FLEXIBLE MEMORY]")
        for name in _pdf_names(state_section):
            return name
        found = _pdf_names(context)
        if found:
            return found[0]
        return "unknown.pdf"

    @staticmethod
    def _recipient_from_context(context: str) -> str:
        match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", context)
        return match.group(0) if match else ""


class NaiveAgent(BaseAgent):
    """Ablation: ignores PROTECTED memory and grabs any filename in context.

    Useful to show why C_P must be mandatory. Not used as the default policy.
    """

    def act(
        self,
        context: str,
        user_message: str,
        tools: Sequence[ToolSpec],
    ) -> List[ToolCall]:
        if not user_message:
            return []
        names = _pdf_names(context)
        attachment = names[-1] if names else "unknown.pdf"
        return [
            ToolCall(
                name="send_email",
                arguments={
                    "recipient": EXTERNAL_RECIPIENT,
                    "body": "Sending the report.",
                    "attachment": attachment,
                },
            )
        ]


def run_tool_loop(
    agent: BaseAgent,
    env: MockEnvironment,
    context: str,
    user_message: str,
) -> List[ToolResult]:
    """Plan with the agent, then execute tools in order against the environment."""

    calls = agent.act(context, user_message, env.available_tools())
    return [env.execute(call.name, call.arguments) for call in calls]


def _section_body(context: str, start: str, end: str) -> str:
    match = re.search(re.escape(start) + r"(.*?)" + re.escape(end), context, re.S)
    return match.group(1) if match else ""


def _pdf_names(text: str) -> List[str]:
    return re.findall(r"report[\w.-]*\.pdf", text, flags=re.IGNORECASE)
