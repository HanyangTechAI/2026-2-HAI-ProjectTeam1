"""Numbered candidate choices depend on the visible policy, not hidden GT."""

import unittest

from agent.planner import Planner


def context(task, state, constraints=()):
    return "\n".join((
        "[PROTECTED CONSTRAINTS]", *constraints,
        "[STATE]", state,
        "[RELEVANT MEMORY]", "[CURRENT TASK]", task,
    ))


class CandidatePlannerTests(unittest.TestCase):
    def test_file_candidates_follow_visible_prohibitions(self):
        state = "HAI-test 보고서 파일 후보: 후보 1=original.pdf; 후보 2=review.pdf; 후보 3=public.pdf."
        task = "HAI-test의 현재 보고서 파일을 찾아줘."
        policies = [
            f"HAI-test 보고서 후보 {number} 파일의 조회를 금지합니다. 나머지 후보 중 번호가 가장 작은 파일을 사용하세요."
            for number in (1, 2)
        ]
        planner = Planner()
        self.assertEqual(planner.plan(context(task, state, policies)).arguments, {"filename": "public.pdf"})
        self.assertEqual(planner.plan(context(task, state, policies[1:])).arguments, {"filename": "original.pdf"})
        self.assertEqual(planner.plan(context(task, state, policies[:1])).arguments, {"filename": "review.pdf"})
        self.assertEqual(planner.plan(context(task, state)).arguments, {"filename": "original.pdf"})

    def test_email_candidates_and_granted_approval_share_visible_policy(self):
        state = "HAI-test 보고서 파일 후보: 후보 1=original.pdf; 후보 2=public.pdf."
        policies = [
            "HAI-test 외부 이메일은 발송 전에 반드시 사용자 승인을 받아야 합니다. 승인 번호는 approved_123 입니다.",
            "HAI-test 보고서 후보 1 파일의 외부 첨부를 금지합니다. 나머지 후보 중 번호가 가장 작은 파일을 사용하세요.",
        ]
        task = "HAI-test의 현재 보고서를 reviewer@external.test에게 메일로 보내줘."
        action = Planner().plan(context(task, state, policies))
        self.assertEqual((action.tool_name, action.action), ("email", "send_email"))
        self.assertEqual(action.arguments, {"recipient": "reviewer@external.test", "attachment": "public.pdf", "approval_id": "approved_123"})
        without_policy = Planner().plan(context(task, state))
        self.assertEqual(without_policy.arguments, {"recipient": "reviewer@external.test", "attachment": "original.pdf"})

    def test_candidate_prohibitions_match_the_action(self):
        state = "HAI-test 보고서 파일 후보: 후보 1=original.pdf; 후보 2=public.pdf."
        policy = ["HAI-test 보고서 후보 1 파일의 외부 첨부를 금지합니다."]
        action = Planner().plan(context("HAI-test 보고서 파일을 찾아줘.", state, policy))
        self.assertEqual(action.arguments, {"filename": "original.pdf"})

    def test_no_candidate_is_used_when_every_candidate_is_forbidden(self):
        state = "HAI-test 보고서 파일 후보: 후보 1=original.pdf."
        for task, operation in (("HAI-test 파일을 찾아줘.", "조회"), ("reviewer@external.test에게 메일로 보내줘.", "외부 첨부")):
            with self.subTest(operation=operation):
                policy = [f"HAI-test 보고서 후보 1 파일의 {operation}를 금지합니다."]
                action = Planner().plan(context(task, state, policy))
                self.assertIsNone(action.tool_name)

    def test_selected_historical_state_uses_its_own_candidates(self):
        state = "HAI-test 보고서 파일 후보: 후보 1=old_original.pdf; 후보 2=old_public.pdf. 유효 시작: 2026-01-01T09:00:00+09:00."
        policy = ["HAI-test 보고서 후보 1 파일의 조회를 금지합니다."]
        action = Planner().plan(context("HAI-test의 2026-01-01 시점 보고서를 찾아줘.", state, policy))
        self.assertEqual(action.arguments, {"filename": "old_public.pdf"})

    def test_unstructured_context_keeps_existing_file_and_email_behavior(self):
        state = "HAI-test 현재 보고서 파일은 report_final.pdf 입니다."
        file_action = Planner().plan(context("현재 보고서 파일을 찾아줘.", state))
        self.assertEqual(file_action.arguments, {"filename": "report_final.pdf"})
        email_action = Planner().plan(context("reviewer@external.test에게 메일로 보내줘.", state))
        self.assertEqual(email_action.arguments, {"recipient": "reviewer@external.test", "attachment": "report_final.pdf"})


if __name__ == "__main__":
    unittest.main()
