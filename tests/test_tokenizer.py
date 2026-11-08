import unittest
import tempfile
import io
import json

from pathlib import Path
from unittest.mock import patch

from context.tokenizer import (
    GeminiTokenCounter,
    TARGET_GEMINI_MODEL,
    count_request_tokens,
    count_tokens,
    provisional_token_count,
)


class FakeCountTokensResponse:
    def __init__(self, total_tokens: int):
        self.total_tokens = total_tokens


class FakeModels:
    def __init__(self):
        self.call_count = 0
        self.calls = []

    def count_tokens(
        self,
        *,
        model,
        contents,
        config=None,
    ):
        self.call_count += 1

        self.calls.append(
            {
                "model": model,
                "contents": contents,
                "config": config,
            }
        )

        total = len(contents)

        if config:
            system_instruction = config.get(
                "system_instruction"
            )

            if system_instruction:
                total += len(system_instruction)

        return FakeCountTokensResponse(
            total_tokens=total
        )


class FakeClient:
    def __init__(self):
        self.models = FakeModels()


class TokenizerTests(unittest.TestCase):
    def test_project_target_model(self):
        self.assertEqual(TARGET_GEMINI_MODEL, "gemini-3.1-flash-lite")

    def test_target_model_is_the_default_without_environment_override(self):
        with patch.dict("os.environ", {}, clear=True):
            counter = GeminiTokenCounter(client=FakeClient())
        self.assertEqual(counter.model, TARGET_GEMINI_MODEL)

    def test_environment_model_overrides_project_default(self):
        with patch.dict("os.environ", {"GEMINI_TOKEN_MODEL": "environment-model"}, clear=True):
            counter = GeminiTokenCounter(client=FakeClient())
        self.assertEqual(counter.model, "environment-model")

    def test_isolated_cache_bypasses_persistent_entries_and_restores_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            counter = GeminiTokenCounter(
                model="fake-gemini", client=client,
                persistent_cache_path=Path(directory) / "tokens.sqlite3",
            )
            self.assertEqual(counter("hello"), 5)
            initial_bytes = (Path(directory) / "tokens.sqlite3").read_bytes()
            for _ in range(2):
                with counter.isolated_cache():
                    before = client.models.call_count
                    self.assertFalse(counter.metadata()["persistent_cache"])
                    self.assertEqual(counter("hello"), 5)
                    self.assertEqual(counter("hello"), 5)
                    self.assertEqual(client.models.call_count - before, 1)
                    counter("uncached text")
                self.assertEqual(counter.cache_info().currsize, 0)
                self.assertTrue(counter.metadata()["persistent_cache"])
            with self.assertRaisesRegex(RuntimeError, "failed run"):
                with counter.isolated_cache():
                    raise RuntimeError("failed run")
            self.assertTrue(counter.metadata()["persistent_cache"])
            self.assertEqual((Path(directory) / "tokens.sqlite3").read_bytes(), initial_bytes)
            before = client.models.call_count
            self.assertEqual(counter("hello"), 5)
            self.assertEqual(client.models.call_count, before)
            counter("uncached text")
            self.assertEqual(client.models.call_count, before + 1)

    def test_provisional_token_count(self):
        self.assertEqual(
            provisional_token_count("hello world"),
            2,
        )


    def test_empty_string(self):
        self.assertEqual(
            provisional_token_count(""),
            0,
        )


    def test_gemini_token_counter(self):
        client = FakeClient()

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
        )

        result = counter("hello")

        self.assertEqual(result, 5)
        self.assertEqual(client.models.call_count, 1)


    def test_gemini_counter_uses_cache(self):
        client = FakeClient()

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
        )

        first = counter("same text")
        second = counter("same text")

        self.assertEqual(first, second)

        # 같은 문자열이므로 실제 API 역할은 한 번만 호출되어야 함
        self.assertEqual(
            client.models.call_count,
            1,
        )


    def test_different_text_is_not_cached_together(self):
        client = FakeClient()

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
        )

        counter("text A")
        counter("text B")

        self.assertEqual(
            client.models.call_count,
            2,
        )


    def test_clear_cache(self):
        client = FakeClient()

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
        )

        counter("hello")
        counter("hello")

        self.assertEqual(
            client.models.call_count,
            1,
        )

        counter.clear_cache()

        counter("hello")

        self.assertEqual(
            client.models.call_count,
            2,
        )


    def test_count_tokens_accepts_gemini_counter(self):
        client = FakeClient()

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
        )

        result = count_tokens(
            "hello",
            token_counter=counter,
        )

        self.assertEqual(result, 5)


    def test_invalid_text_type(self):
        client = FakeClient()

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
        )

        with self.assertRaises(TypeError):
            counter(123)


    def test_missing_model(self):
        client = FakeClient()

        with self.assertRaises(ValueError):
            GeminiTokenCounter(
                model="",
                client=client,
            )
            
            
    def test_persistent_cache_survives_new_counter(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = (
                Path(tmp)
                / "tokens.sqlite3"
            )

            first_client = FakeClient()

            first = GeminiTokenCounter(
                model="fake-gemini",
                client=first_client,
                persistent_cache_path=cache_path,
            )

            self.assertEqual(
                first("hello"),
                5,
            )

            self.assertEqual(
                first_client.models.call_count,
                1,
            )

            # 새로운 Counter = 새로운 프로세스 실행을 모사
            second_client = FakeClient()

            second = GeminiTokenCounter(
                model="fake-gemini",
                client=second_client,
                persistent_cache_path=cache_path,
            )

            self.assertEqual(
                second("hello"),
                5,
            )

            # API는 호출되지 않아야 한다.
            self.assertEqual(
                second_client.models.call_count,
                0,
            )

            self.assertEqual(
                second.stats()["persistent_hits"],
                1,
            )


    def test_retry_transient_error(
        self,
    ):
        client = TransientFakeClient()

        delays = []

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
            max_retries=3,
            retry_base_seconds=0.1,
            sleeper=delays.append,
        )

        self.assertEqual(
            counter("hello"),
            5,
        )

        self.assertEqual(
            client.models.call_count,
            3,
        )

        self.assertEqual(
            counter.stats()["retries"],
            2,
        )

        self.assertEqual(
            delays,
            [
                0.1,
                0.2,
            ],
        )


    def test_permanent_error_is_not_retried(
        self,
    ):
        client = PermanentFakeClient()

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
            max_retries=3,
            sleeper=lambda _: None,
        )

        with self.assertRaises(
            ValueError
        ):
            counter("hello")

        self.assertEqual(
            client.models.call_count,
            1,
        )


    def test_failed_request_is_not_cached(
        self,
    ):
        client = PermanentFakeClient()

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
            max_retries=0,
        )

        for _ in range(2):
            with self.assertRaises(
                ValueError
            ):
                counter("hello")

        # 실패 결과는 LRU에도 cache되지 않는다.
        self.assertEqual(
            client.models.call_count,
            2,
        )


    def test_different_models_do_not_share_cache(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                Path(tmp)
                / "tokens.sqlite3"
            )

            client_a = FakeClient()

            counter_a = GeminiTokenCounter(
                model="model-a",
                client=client_a,
                persistent_cache_path=path,
            )

            counter_a("hello")

            client_b = FakeClient()

            counter_b = GeminiTokenCounter(
                model="model-b",
                client=client_b,
                persistent_cache_path=path,
            )

            counter_b("hello")

            self.assertEqual(
                client_b.models.call_count,
                1,
            )
            
            
    def test_request_count_includes_system_instruction(self):
        client = FakeClient()

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
            api_key="test-key",
        )

        counter.configure_request(
            system_instruction="SYSTEM",
            generation_config={
                "system_instruction": "SYSTEM",
            },
        )

        # 일반 Memory 비용 측정은 기존 SDK 사용
        self.assertEqual(counter("hello"), 5)

        fake_result = io.BytesIO(
            b'{"totalTokens": 11}'
        )

        with patch(
            "context.tokenizer.urlopen",
            return_value=fake_result,
        ) as mocked:
            self.assertEqual(
                counter.count_request("hello"),
                11,
            )

        request = mocked.call_args.args[0]
        body = json.loads(request.data)

        full_request = body["generateContentRequest"]

        self.assertEqual(
            full_request["model"],
            "models/fake-gemini",
        )
        self.assertEqual(
            full_request["systemInstruction"]["parts"][0]["text"],
            "SYSTEM",
        )
        self.assertEqual(
            full_request["contents"][0]["parts"][0]["text"],
            "hello",
        )
        self.assertEqual(
            counter.metadata()["request_count_transport"],
            "developer_api_rest",
        )


    def test_request_count_does_not_change_bare_memory_count(self):
        client = FakeClient()

        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=client,
            api_key="test-key",
        )

        counter.configure_request(
            system_instruction="SYSTEM",
        )

        bare = count_tokens(
            "memory",
            counter,
        )

        with patch(
            "context.tokenizer.urlopen",
            return_value=io.BytesIO(b'{"totalTokens": 12}'),
        ):
            request = count_request_tokens(
                "memory",
                counter,
            )

        self.assertEqual(bare, 6)
        self.assertEqual(request, 12)
        
        
    def test_request_configuration_mismatch_is_rejected(self):
        counter = GeminiTokenCounter(
            model="fake-gemini",
            client=FakeClient(),
        )

        with self.assertRaisesRegex(
            ValueError,
            "does not match",
        ):
            counter.configure_request(
                system_instruction="A",
                generation_config={
                    "system_instruction": "B",
                },
            )
            
            
class TransientFakeModels:
    def __init__(self):
        self.call_count = 0

    def count_tokens(
        self,
        *,
        model,
        contents,
    ):
        self.call_count += 1

        if self.call_count < 3:
            raise TimeoutError(
                "temporary timeout"
            )

        return FakeCountTokensResponse(
            total_tokens=len(contents)
        )


class TransientFakeClient:
    def __init__(self):
        self.models = TransientFakeModels()


class PermanentFakeModels:
    def __init__(self):
        self.call_count = 0

    def count_tokens(
        self,
        *,
        model,
        contents,
    ):
        self.call_count += 1

        raise ValueError(
            "invalid request"
        )


class PermanentFakeClient:
    def __init__(self):
        self.models = PermanentFakeModels()


if __name__ == "__main__":
    unittest.main()
