from __future__ import annotations

from datetime import datetime, timezone
import unittest

from agent.agent import Agent
from context.tokenizer import provisional_token_count
from memory.schema import (
    MemoryItem,
    MemoryType,
)
from pipeline.pipeline_adapter import (
    make_proposed_pipeline_adapter,
)


NOW = datetime(
    2026,
    11,
    2,
    tzinfo=timezone.utc,
)


def fake_embedder(
    text: str,
):
    text = text.casefold()

    return (
        float(
            "보고서" in text
            or "report" in text
        ),
        float(
            "메일" in text
            or "email" in text
        ),
    )


class ProposedPipelineTests(
    unittest.TestCase,
):

    def test_proposed_policy_preserves_current_state(self):
        from agent.planner import Planner
        from agent.tools.executor import ToolExecutor
        from agent.tools.file import FileTool

        old = MemoryItem(
            memory_id="report_v1",
            session_id="session",
            turn_id=10,
            memory_type=MemoryType.STATE,
            content="report_v1.pdf",
            token_count=10,
            created_at=datetime(
                2026,
                8,
                1,
                tzinfo=timezone.utc,
            ),
            valid_from=datetime(
                2026,
                8,
                1,
                tzinfo=timezone.utc,
            ),
            valid_to=datetime(
                2026,
                9,
                1,
                tzinfo=timezone.utc,
            ),
            memory_key="report.current_file",
            is_active=False,
            superseded_by="report_final",
        )

        current = MemoryItem(
            memory_id="report_final",
            session_id="session",
            turn_id=50,
            memory_type=MemoryType.STATE,
            content="report_final.pdf",
            token_count=10,
            created_at=datetime(
                2026,
                9,
                1,
                tzinfo=timezone.utc,
            ),
            valid_from=datetime(
                2026,
                9,
                1,
                tzinfo=timezone.utc,
            ),
            memory_key="report.current_file",
            is_active=True,
            supersedes="report_v1",
        )

        agent = Agent(
            planner=Planner(),
            executor=ToolExecutor(
                file=FileTool(
                    {
                        "report_final.pdf":
                            "final"
                    }
                )
            ),
        )

        adapter = (
            make_proposed_pipeline_adapter(
                system_prompt=(
                    "기억을 참고해 "
                    "요청을 수행한다."
                ),
                tool_definitions=(
                    "file.find_file(filename)"
                ),
                agent_factory=lambda: agent,
                embedder=fake_embedder,
                token_counter=(
                    provisional_token_count
                ),
                max_steps=1,
            )
        )

        result = adapter(
            query="현재 보고서 파일을 찾아줘.",
            session_id="session",
            memories=(
                old,
                current,
            ),
            total_budget=1000,
            current_turn=100,
            current_time=NOW,
        )

        selection = (
            result.steps[0]
            .selection_result
        )

        self.assertIn(
            "report_final",
            selection.selected_memory_ids,
        )

        self.assertNotIn(
            "report_v1",
            selection.selected_memory_ids,
        )


if __name__ == "__main__":
    unittest.main()