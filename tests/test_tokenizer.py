import unittest

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


if __name__ == "__main__":
    unittest.main()