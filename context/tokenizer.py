from __future__ import annotations

import os
from collections.abc import Callable
from functools import lru_cache
from typing import Any


TokenCounter = Callable[[str], int]


def provisional_token_count(text: str) -> int:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    
    if not text:
        return 0
    
    return len(text.split())


class GeminiTokenCounter:
    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        cache_size: int = 4096,
        client: Any | None = None,
    ):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        
        if cache_size < 0:
            raise ValueError("cache_size must be >= 0")
        
        self.model = model.strip()
        
        if client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise ImportError("google-genai is required gor GeminiTokenCounter. Install it with: pip install google-genai") from exc
            
            resolved_api_key = api_key or os.getenv("GEMINI_API_KEY")
            
            if not resolved_api_key:
                raise ValueError("Gemini API key is required. Pass api_key=... or set GEMINI_API_KEY.")
            
            client = genai.Client(api_key=resolved_api_key)
            
        self._client = client
        
        self._cached_count = lru_cache(maxsize=cache_size)(self._count_tokens_from_api)
        
    def __call__(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        
        if not text:
            return 0
        
        return self._cached_count(text)
    
    def _count_tokens_from_api(self, text: str) -> int:
        response = self._client.models.count_tokens(
            model=self.model,
            contents=text,
        )
        
        total_tokens = getattr(response, "total_tokens", None)
        
        if type(total_tokens) is not int:
            raise RuntimeError(f"Gemini CountTokens returned an invalid total_tokens value: {total_tokens!r}")
        
        if total_tokens < 0:
            raise RuntimeError("Gemini CountTokens returned a negative token count")
        
        return total_tokens
    
    def clear_cache(self) -> None:
        self._cached_count.cache_clear()
        
    def cache_info(self):
        return self._cached_count.cache_info()


def count_tokens(
    text: str,
    token_counter: TokenCounter | None = None,
) -> int:
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    counter = token_counter or provisional_token_count
    count = counter(text)
    
    if type(count) is not int:
        raise TypeError("token_counter must return an integer")
    
    if count < 0:
        raise ValueError("token_counter must return a non-negative integer")
    
    return count


def token_count_mode(token_counter: TokenCounter) -> str:
    if token_counter is provisional_token_count:
        return "provisional"
    
    if isinstance(token_counter, GeminiTokenCounter):
        return f"gemini:{token_counter.model}"
    
    return "custom"