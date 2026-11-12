"""Tests for Critical-Flexible Stress v1.0."""
import copy
import tempfile
import unittest

from collections import Counter
from datetime import datetime
from pathlib import Path

from benchmark.critical_flexible_generator import (
    CONDITIONS,
    TOKEN_BANDS,
    generate_dataset,
    make_scenario,
    save_dataset,
    validate_scenario,
)
from benchmark.evaluator import evaluate

from context.tokenizer import provisional_token_count

from memory.ablation import get_ablation_config
from memory.schema import MemoryItem
from memory.selector import PipelineStatus, select_memory
from memory.store import SnapshotMemoryStore


class CriticalFlexibleGeneratorTests(unittest.TestCase):

    def setUp(self):
        self.counter = provisional_token_count

    def test_shape_distribution_determinism_and_pairing(self):
        a = generate_dataset(
            counter=self.counter,
            cases_per_family=2,
        )

        b = generate_dataset(
            counter=self.counter,
            cases_per_family=2,
        )

        self.assertEqual(a, b)

        scenarios = a["scenarios"]

        self.assertEqual(len(scenarios), 16)

        self.assertEqual(
            len({
                s["scenario_id"]
                for s in scenarios
            }),
            16,
        )

        self.assertEqual(
            Counter(
                s["metadata"]["stress_condition"]
                for s in scenarios
            ),
            dict.fromkeys(CONDITIONS, 4),
        )

        self.assertEqual(
            Counter(
                s["metadata"]["task_family"]
                for s in scenarios
            ),
            {
                "file_lookup": 8,
                "approved_email": 8,
            },
        )

        bases = {}

        for s in scenarios:
            base = s["metadata"]["base_case_id"]
            bases.setdefault(base, []).append(s)

        self.assertEqual(len(bases), 4)

        for variants in bases.values():
            self.assertEqual(
                {
                    s["metadata"]["stress_condition"]
                    for s in variants
                },
                set(CONDITIONS),
            )

            self.assertEqual(
                len({
                    s["query"]
                    for s in variants
                }),
                1,
            )

            self.assertEqual(
                len({
                    str(s["expected_tool_calls"])
                    for s in variants
                }),
                1,
            )

    def test_memory_lengths_labels_and_no_leakage(self):
        families = (
            "file_lookup",
            "approved_email",
        )

        for family in families:
            for condition in CONDITIONS:
                with self.subTest(
                    family=family,
                    condition=condition,
                ):
                    s = make_scenario(
                        family,
                        42,
                        condition,
                        counter=self.counter,
                    )

                    validate_scenario(
                        s,
                        self.counter,
                    )

                    raw = s["memory_snapshot"]

                    ids = {
                        m["memory_id"]: m
                        for m in raw
                    }

                    meta = s["metadata"]

                    critical_id = (
                        meta["critical_flexible_memory_ids"][0]
                    )

                    critical = ids[critical_id]

                    cband, nband = CONDITIONS[condition]

                    lo, hi = TOKEN_BANDS[cband]

                    self.assertTrue(
                        lo <= critical["token_count"] <= hi
                    )

                    for nid in meta["distractor_memory_ids"]:
                        noise = ids[nid]

                        lo, hi = TOKEN_BANDS[nband]

                        self.assertTrue(
                            lo <= noise["token_count"] <= hi
                        )

                        self.assertEqual(
                            noise["memory_type"],
                            "fact",
                        )

                        self.assertFalse(
                            noise["is_protected"]
                        )

                    answer_arg = (
                        "filename"
                        if family == "file_lookup"
                        else "approval_id"
                    )

                    answer = (
                        s["expected_tool_calls"][0]
                        ["arguments"][answer_arg]
                    )

                    self.assertEqual(
                        sum(
                            answer in m["content"]
                            for m in raw
                        ),
                        1,
                    )

                    self.assertEqual(
                        critical["content"].count(answer),
                        1,
                    )

                    self.assertNotIn(
                        answer,
                        s["query"],
                    )

    def test_selector_integration_under_roomy_budget(self):
        for family in (
            "file_lookup",
            "approved_email",
        ):
            s = make_scenario(
                family,
                43,
                "C",
                counter=self.counter,
            )

            memories = tuple(
                MemoryItem.from_dict(x)
                for x in s["memory_snapshot"]
            )

            store = SnapshotMemoryStore(memories)

            expected_critical = (
                s["metadata"]
                ["critical_flexible_memory_ids"][0]
            )

            for preset in (
                "full",
                "no_budget_aware_packing",
            ):
                with self.subTest(
                    family=family,
                    preset=preset,
                ):
                    result = select_memory(
                        query=s["query"],
                        session_id=s["session_id"],
                        memory_store=store,

                        total_budget=10000,
                        fixed_tokens=20,

                        current_turn=s["current_turn"],
                        current_time=datetime.fromisoformat(
                            s["current_time"]
                        ),

                        # 테스트 전용 결정적 임베딩
                        embedder=lambda text: (1.0, 0.0),

                        token_counter=self.counter,
                        top_k=5,

                        ablation=get_ablation_config(
                            preset
                        ),
                    )

                    self.assertEqual(
                        result.status,
                        PipelineStatus.OK,
                    )

                    self.assertIn(
                        expected_critical,
                        result.selected_memory_ids,
                    )

                    if family == "approved_email":
                        self.assertEqual(
                            len(result.states),
                            1,
                        )

                        self.assertEqual(
                            len(result.protected),
                            1,
                        )
                    else:
                        self.assertEqual(
                            len(result.states),
                            0,
                        )

                        self.assertEqual(
                            len(result.protected),
                            0,
                        )

    def test_evaluator_contract_and_missing_critical_argument(self):
        for family in (
            "file_lookup",
            "approved_email",
        ):
            s = make_scenario(
                family,
                44,
                "C",
                counter=self.counter,
            )

            all_ids = [
                x["memory_id"]
                for x in s["memory_snapshot"]
            ]

            call = copy.deepcopy(
                s["expected_tool_calls"][0]
            )

            # 실제 Gemini가 아니라
            # 올바른 행동을 한 가상의 실행 기록을 구성한다.
            run = {
                "status": "ok",
                "goal_completed": True,

                "tool_calls": [call],

                "selection_result": {
                    "retrieved_memory_ids": all_ids,
                    "selected_memory_ids": all_ids,
                },

                "context_result": {
                    "selected_memory_ids": all_ids,
                },

                "usage": {
                    "input_tokens": 100,
                    "fixed_input_tokens": 20,
                },
            }

            good = evaluate(
                s,
                run,
                strategy="proposed",
            )

            self.assertTrue(
                good.task_success,
                (
                    family,
                    good.metrics.get("failure_types"),
                ),
            )

            # Critical FACT에 담긴 필수 인자를 제거하면
            # 정답 행동으로 인정되지 않아야 한다.
            bad = copy.deepcopy(run)

            field = (
                "filename"
                if family == "file_lookup"
                else "approval_id"
            )

            bad["tool_calls"][0]["arguments"].pop(field)

            broken = evaluate(
                s,
                bad,
                strategy="proposed",
            )

            self.assertFalse(
                broken.task_success
            )

    def test_rejects_answer_leakage(self):
        s = make_scenario(
            "approved_email",
            44,
            "A",
            counter=self.counter,
        )

        answer = (
            s["expected_tool_calls"][0]
            ["arguments"]["approval_id"]
        )

        noise_id = (
            s["metadata"]["distractor_memory_ids"][0]
        )

        # Noise에 정답을 고의로 삽입한다.
        for memory in s["memory_snapshot"]:
            if memory["memory_id"] == noise_id:
                memory["content"] += " " + answer
                memory["token_count"] = self.counter(
                    memory["content"]
                )

        with self.assertRaises(ValueError):
            validate_scenario(
                s,
                self.counter,
            )

    def test_save_does_not_overwrite(self):
        data = generate_dataset(
            counter=self.counter,
            cases_per_family=1,
        )

        with tempfile.TemporaryDirectory() as d:
            path = (
                Path(d)
                / "generated"
                / "dataset.json"
            )

            manifest = save_dataset(
                data,
                path,
            )

            self.assertEqual(
                manifest["scenario_count"],
                8,
            )

            self.assertEqual(
                manifest["base_case_count"],
                2,
            )

            self.assertTrue(path.exists())

            self.assertTrue(
                path.with_name(
                    "dataset_manifest.json"
                ).exists()
            )

            with self.assertRaises(FileExistsError):
                save_dataset(
                    data,
                    path,
                )

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            make_scenario(
                "approved_email",
                42,
                "E",
                counter=self.counter,
            )

        with self.assertRaises(ValueError):
            generate_dataset(
                counter=self.counter,
                cases_per_family=0,
            )


if __name__ == "__main__":
    unittest.main()