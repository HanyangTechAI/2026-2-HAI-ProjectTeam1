import unittest

from agent.agent import AgentAction
from agent.llm_planner import _fill_missing_attachment


class LLMPlannerAttachmentResolutionTests(unittest.TestCase):
    def test_fills_first_allowed_candidate_from_visible_context(self):
        context = """[PROTECTED CONSTRAINTS]
- 보고서 후보 1 파일의 외부 첨부를 금지
[STATE]
- 보고서 파일 후보: 후보 1=private.pdf, 후보 2=final.pdf
[CURRENT TASK]
- 교수님께 보고서를 보내기 위해 승인을 요청해.
"""
        action = AgentAction("email", "request_approval", {"recipient": "reviewer@example.test"}, None)

        resolved = _fill_missing_attachment(action, context)

        self.assertEqual(resolved.arguments["attachment"], "final.pdf")
        self.assertEqual(resolved.arguments["recipient"], "reviewer@example.test")

    def test_returns_text_when_every_visible_candidate_is_blocked(self):
        context = """[PROTECTED CONSTRAINTS]
- 보고서 후보 1 파일의 외부 첨부를 금지
[STATE]
- 보고서 파일 후보: 후보 1=private.pdf
[CURRENT TASK]
- 교수님께 보내기 위해 승인을 요청해.
"""
        action = AgentAction("email", "request_approval", {"recipient": "reviewer@example.test"}, None)

        resolved = _fill_missing_attachment(action, context)

        self.assertIsNone(resolved.tool_name)
        self.assertIsNone(resolved.action)
        self.assertIn("후보가 없습니다", resolved.text_response)

    def test_leaves_unrelated_or_already_complete_actions_unchanged(self):
        context = "[STATE]\n- report_final.pdf\n"
        lookup = AgentAction("file", "search", {"query": "report"}, None)
        complete = AgentAction("email", "send_email", {"attachment": "report_final.pdf"}, None)

        self.assertIs(_fill_missing_attachment(lookup, context), lookup)
        self.assertIs(_fill_missing_attachment(complete, context), complete)

    def test_does_not_invent_an_attachment_when_context_has_no_file(self):
        action = AgentAction("email", "request_approval", {"recipient": "reviewer@example.test"}, None)

        self.assertIs(_fill_missing_attachment(action, "[STATE]\n- no file is listed\n"), action)


if __name__ == "__main__":
    unittest.main()
