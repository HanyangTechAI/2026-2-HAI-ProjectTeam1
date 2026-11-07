import unittest
from unittest.mock import patch

from benchmark.experiment_runtime import (
    _cached_model_revision,
    embedding_runtime_metadata,
)
from benchmark.experiment_settings import (
    EXPERIMENT_EMBEDDING_MODEL,
)


class ExperimentRuntimeMetadataTests(
    unittest.TestCase
):
    @patch(
        "benchmark.experiment_runtime."
        "try_to_load_from_cache"
    )
    def test_cached_revision_is_extracted(
        self,
        cached,
    ):
        cached.return_value = (
            "C:/cache/"
            "models--sentence-transformers--model/"
            "snapshots/"
            + ("a" * 40)
            + "/config.json"
        )

        revision = _cached_model_revision(
            EXPERIMENT_EMBEDDING_MODEL
        )

        self.assertEqual(
            revision,
            "a" * 40,
        )

    @patch(
        "benchmark.experiment_runtime."
        "_get_model"
    )
    @patch(
        "benchmark.experiment_runtime."
        "_cached_model_revision"
    )
    def test_embedding_metadata(
        self,
        revision,
        get_model,
    ):
        revision.return_value = "b" * 40

        metadata = (
            embedding_runtime_metadata()
        )

        get_model.assert_called_once()

        self.assertEqual(
            metadata["model"],
            EXPERIMENT_EMBEDDING_MODEL,
        )

        self.assertEqual(
            metadata["resolved_revision"],
            "b" * 40,
        )

        self.assertTrue(
            metadata[
                "normalize_embeddings"
            ]
        )