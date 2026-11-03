import unittest
import tempfile

from pathlib import Path

from context.tokenizer import (
    GeminiTokenCounter,
    count_tokens,
    provisional_token_count,
)


class FakeCountTokensResponse:
    def __init__(self, total_tokens: int):
        self.total_tokens = total_tokens


class FakeModels:
    def __init__(self):
        self.call_count = 0

    def count_tokens(self, *, model, contents):
        self.call_count += 1

        # 테스트를 위한 deterministic fake 결과
        return FakeCountTokensResponse(
            total_tokens=len(contents)
        )


class FakeClient:
    def __init__(self):
        self.models = FakeModels()


class TokenizerTests(unittest.TestCase):
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