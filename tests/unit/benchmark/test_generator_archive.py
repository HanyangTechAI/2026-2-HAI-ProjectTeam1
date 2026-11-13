"""Archive outputs preserve runnable cells without storing repeated raw text."""

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from benchmark.generator import generate_dataset, write_dataset_archive


class GeneratorArchiveTests(unittest.TestCase):
    def test_archive_is_reproducible_and_contains_complete_cells(self):
        config = {
            "dataset": {"source": "synthetic", "samples_per_cell": 2,
                        "task_types": ["temporal_update", "approval"]},
            "seeds": [42],
            "grid": {
                "context_token_budget": [1024, 2048], "history_length_tokens": [10000],
                "horizon_turns": [10], "noise_memory_ratio": [0.5],
                "constraint_count": [1], "state_update_count": [2],
            },
        }
        payload = generate_dataset(config)
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "first.zip", Path(directory) / "second.zip"
            manifest = write_dataset_archive(payload, first)
            self.assertEqual(manifest["scenario_profile"], "balanced_v2")
            write_dataset_archive(payload, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(json.loads(archive.read("manifest.json")), manifest)
                self.assertEqual(len(archive.namelist()), 3)
                for cell in manifest["cells"]:
                    record = json.loads(archive.read(cell["path"]))
                    self.assertEqual(record["scenario_profile"], manifest["scenario_profile"])
                    self.assertEqual(record["coverage"], cell["coverage"])
                    self.assertEqual(record["coverage"]["constraint_applicable_scenario_count"], 2)
                    self.assertEqual(record["coverage"]["applicable_constraint_count"], 2)
                    self.assertEqual(record["parameters"], cell["parameters"])
                    self.assertEqual(len(record["scenarios"]), 2)
                    self.assertTrue(all(s["history"] for s in record["scenarios"]))

    def test_wrong_archive_extension_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "data.json"
            with self.assertRaisesRegex(ValueError, "zip"):
                write_dataset_archive({}, target)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
