"""Query Analyzer 검증만 수행한다. 구현 파일은 변경하지 않는다.

근거: interfaces.md 6절, algorithm.md 시간 의도 예시, edge_cases.md EC-03/05/09.
실행: python -B -m unittest memory.test_query_analyzer
"""

from datetime import datetime, timedelta, timezone
import unittest

from memory.query_analyzer import TemporalIntent, analyze_query


KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 5, 12, tzinfo=KST)


class QueryAnalyzerTests(unittest.TestCase):
    def analyze(self, query, now=NOW):
        return analyze_query(query, 100, now)

    def test_current_expressions(self):
        for query in ("지금 보고서 뭐야?", "현재 보고서 찾아줘", "최신 보고서 알려줘"):
            with self.subTest(query=query):
                self.assertEqual(self.analyze(query).temporal_intent, TemporalIntent.CURRENT)

    def test_unspecified_ec05(self):
        result = self.analyze("보고서 파일 알려줘")
        self.assertEqual(result.temporal_intent, TemporalIntent.UNSPECIFIED)
        self.assertIsNone(result.target_time)
        self.assertIsNone(result.target_range)

    def test_absolute_timestamp(self):
        result = self.analyze("2026-08-20T00:00:00+09:00 당시 보고서")
        self.assertEqual(result.temporal_intent, TemporalIntent.HISTORICAL)
        self.assertEqual(result.target_time, datetime(2026, 8, 20, tzinfo=KST))

    def test_absolute_date(self):
        result = self.analyze("2024-08-01 당시 파일")
        self.assertEqual(result.temporal_intent, TemporalIntent.HISTORICAL)
        self.assertEqual(result.target_time.date(), datetime(2024, 8, 1).date())

    def test_korean_date(self):
        result = self.analyze("2024년 8월 20일 보고서")
        self.assertEqual(result.temporal_intent, TemporalIntent.HISTORICAL)
        self.assertEqual(result.target_time, datetime(2024, 8, 20, tzinfo=KST))

    def test_month_range_document_example(self):
        result = self.analyze("8월에 쓰던 보고서를 찾아줘")
        self.assertEqual(result.temporal_intent, TemporalIntent.HISTORICAL)
        self.assertEqual(result.target_range, (datetime(2026, 8, 1, tzinfo=KST), datetime(2026, 9, 1, tzinfo=KST)))

    def test_year_range_ec03(self):
        result = self.analyze("2024년에 사용하던 파일")
        self.assertEqual(result.temporal_intent, TemporalIntent.HISTORICAL)
        self.assertEqual(result.target_range, (datetime(2024, 1, 1, tzinfo=KST), datetime(2025, 1, 1, tzinfo=KST)))

    def test_relative_two_months_document_example(self):
        result = self.analyze("두 달 전에 쓰던 파일")
        self.assertEqual(result.temporal_intent, TemporalIntent.RELATIVE_TIME)
        self.assertEqual(result.target_time, datetime(2026, 8, 5, 12, tzinfo=KST))

    def test_relative_units(self):
        cases = [("1개월 전", NOW.replace(month=9)), ("1년 전", NOW.replace(year=2025)),
                 ("2주 전", NOW - timedelta(weeks=2)), ("3일 전", NOW - timedelta(days=3)),
                 ("2시간 후", NOW + timedelta(hours=2))]
        for phrase, expected in cases:
            with self.subTest(phrase=phrase):
                result = self.analyze(phrase + " 파일")
                self.assertEqual(result.temporal_intent, TemporalIntent.RELATIVE_TIME)
                self.assertEqual(result.target_time, expected)

    def test_relative_uses_supplied_clock(self):
        first = self.analyze("두 달 전 파일", NOW)
        second = self.analyze("두 달 전 파일", NOW.replace(year=2025))
        self.assertEqual(first.target_time.year, 2026)
        self.assertEqual(second.target_time.year, 2025)

    def test_last_month_range(self):
        result = self.analyze("지난달 보고서")
        self.assertEqual(result.temporal_intent, TemporalIntent.RELATIVE_TIME)
        self.assertEqual(result.target_range, (datetime(2026, 9, 1, tzinfo=KST), datetime(2026, 10, 1, tzinfo=KST)))

    def test_last_week_range(self):
        result = self.analyze("지난주 보고서")
        self.assertEqual(result.temporal_intent, TemporalIntent.RELATIVE_TIME)
        self.assertEqual(result.target_range, (datetime(2026, 9, 28, tzinfo=KST), datetime(2026, 10, 5, tzinfo=KST)))

    def test_last_year_range(self):
        result = self.analyze("작년 보고서")
        self.assertEqual(result.temporal_intent, TemporalIntent.RELATIVE_TIME)
        self.assertEqual(result.target_range, (datetime(2025, 1, 1, tzinfo=KST), datetime(2026, 1, 1, tzinfo=KST)))

    def test_day_expressions(self):
        for phrase, days in (("어제", -1), ("오늘", 0), ("내일", 1)):
            with self.subTest(phrase=phrase):
                result = self.analyze(phrase + " 일정")
                self.assertEqual(result.temporal_intent, TemporalIntent.RELATIVE_TIME)
                self.assertEqual(result.target_time, NOW + timedelta(days=days))

    def test_month_end_clamping_existing_policy(self):
        """현재 구현의 달력 말일 정책 검증; 별도의 새 요구를 추가하지 않는다."""
        result = self.analyze("한 달 전 파일", datetime(2024, 3, 31, 12, tzinfo=KST))
        self.assertEqual(result.target_time, datetime(2024, 2, 29, 12, tzinfo=KST))

    def test_state_time_distinct_from_action_time_ec09(self):
        result = self.analyze("6월 보고서를 지금 교수님께 보내줘")
        self.assertEqual(result.temporal_intent, TemporalIntent.HISTORICAL)
        self.assertEqual(result.target_range, (datetime(2026, 6, 1, tzinfo=KST), datetime(2026, 7, 1, tzinfo=KST)))
        self.assertEqual(result.current_time, NOW)
        self.assertIn("email", result.required_tools)

    def test_query_metadata_preserved(self):
        query = "보고서 파일 찾아줘"
        result = self.analyze(query)
        self.assertEqual((result.query, result.current_turn, result.current_time), (query, 100, NOW))
        self.assertIn("보고서", result.entities)
        self.assertIn("file", result.required_tools)

    def test_entity_filename_and_recipient(self):
        result = self.analyze("교수님께 report_final.pdf를 이메일로 보내줘")
        self.assertIn("report_final.pdf", result.entities)
        self.assertIn("교수님", result.entities)
        self.assertIn("email", result.required_tools)

    def test_unsupported_explicit_past_rejected(self):
        with self.assertRaises(ValueError):
            self.analyze("그 당시에 쓰던 파일")

    def test_invalid_inputs(self):
        for query in ("", "   ", "2026-02-30 당시 파일"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.analyze(query)
        with self.assertRaises(ValueError):
            analyze_query("파일", -1, NOW)
        with self.assertRaises(TypeError):
            analyze_query("파일", 1, None)


if __name__ == "__main__":
    unittest.main()
