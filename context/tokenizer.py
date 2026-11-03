from __future__ import annotations

import os
import hashlib
import sqlite3
import time

from collections.abc import Callable
from functools import lru_cache
from typing import Any
from pathlib import Path
from contextlib import closing, contextmanager


TokenCounter = Callable[[str], int]


def provisional_token_count(text: str) -> int:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    
    if not text:
        return 0
    
    return len(text.split())


class GeminiTokenCounter:
    RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
    
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        cache_size: int = 4096,
        persistent_cache_path: str | Path | None = None,
        max_retries: int = 3,
        retry_base_seconds: float = 0.5,
        client: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        model = model if model is not None else os.getenv("GEMINI_TOKEN_MODEL")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string; set GEMINI_TOKEN_MODEL or pass model")
        
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        
        if isinstance(retry_base_seconds, bool) or not isinstance(retry_base_seconds, (int, float)) or retry_base_seconds < 0:
            raise ValueError("retry_base_seconds must be non-negative")
        
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        
        self.model = model.strip()
        self.cache_size = cache_size
        
        self.max_retries = max_retries
        self.retry_base_seconds = float(retry_base_seconds)
        
        self.sleeper = sleeper

        if client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise ImportError("google-genai is required for GeminiTokenCounter. Install it with: pip install google-genai") from exc
            
            resolved_api_key = api_key or os.getenv("GEMINI_API_KEY")
            
            if not resolved_api_key:
                raise ValueError("Gemini API key is required. Pass api_key=... or set GEMINI_API_KEY.")
            
            client = genai.Client(api_key=resolved_api_key)
            
        self._client = client
        
        self._persistent_cache_path: Path | None = None
        
        if persistent_cache_path is not None:
            path = Path(persistent_cache_path)
            
            path.parent.mkdir(parents=True, exist_ok=True)
            
            self._persistent_cache_path = path
            
            self._initialize_persistent_cache()
            
        self._api_calls = 0
        self._persistent_hits = 0
        self._retry_count = 0
        
        self._cached_count = lru_cache(maxsize=cache_size)(self._count_tokens_cached)
        
    def __call__(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        
        if not text:
            return 0
        
        return self._cached_count(text)
    
    
    def _count_tokens_cached(self, text: str) -> int:
        cached = self._read_persistent_cache(text)
        
        if cached is not None:
            self._persistent_hits += 1
            return cached
        
        token_count = self._count_tokens_from_api(text)
        
        self._write_persistent_cache(text, token_count)
        
        return token_count
    
    
    def _count_tokens_from_api(self, text: str) -> int:
        last_error: Exception | None = None
        
        for attempt in range(self.max_retries + 1):
            try:
                self._api_calls += 1
                
                response = self._client.models.count_tokens(
                    model=self.model,
                    contents=text,
                )
                
                total_tokens = getattr(response, "total_tokens", None)
                
                if type(total_tokens) is not int:
                    raise RuntimeError(f"Gemini CountTokens returned an invalid token_tokens value: {total_tokens!r}")
                
                if total_tokens < 0:
                    raise RuntimeError("Gemini CountTokens returned a negative token count")
                
                return total_tokens
            
            except Exception as exc:
                last_error = exc
                
                if attempt >= self.max_retries or not self._is_retryable_error(exc):
                    raise
                
                self._retry_count += 1
                
                delay = self.retry_base_seconds * (2 ** attempt)
                
                self.sleeper(delay)
                
        raise RuntimeError("Gemini CountTokens failed") from last_error
    
    
    @classmethod
    def _is_retryable_error(cls, exc: Exception) -> bool:
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return True
        
        status = getattr(exc, "status_code", None)
        
        if status is None:
            status = getattr(exc, "code", None)
            
        if hasattr(status, "value"):
            status = status.value
            
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = None
            
        return status in cls.RETRYABLE_STATUS_CODES
    
    
    def _initialize_persistent_cache(self) -> None:
        if self._persistent_cache_path is None:
            return
        
        with closing(sqlite3.connect(self._persistent_cache_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS token_cache (
                    model TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    token_count INTEGER NOT NULL,
                    PRIMARY KEY (
                        model,
                        text_hash
                    )
                )
                """
            )
            
            connection.commit()
            
            
    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    
    
    def _read_persistent_cache(self, text: str) -> int | None:
        if self._persistent_cache_path is None:
            return None
        
        text_hash = self._hash_text(text)
        
        with closing(sqlite3.connect(self._persistent_cache_path)) as connection:
            row = connection.execute(
                """
                SELECT token_count
                FROM token_cache
                WHERE model = ? AND text_hash = ?
                """,
                (self.model, text_hash),
            ).fetchone()
            
        if row is None:
            return None
        
        token_count = row[0]
        
        if type(token_count) is not int or token_count < 0:
            raise RuntimeError("Persistent cache contains an invalid token count")
            
        return token_count
    
    
    def _write_persistent_cache(self, text: str, token_count: int) -> None:
        if self._persistent_cache_path is None:
            return
        
        text_hash = self._hash_text(text)
        
        with closing(sqlite3.connect(self._persistent_cache_path)) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO token_cache (model, text_hash, token_count)
                VALUES (?, ?, ?)
                """,
                (self.model, text_hash, token_count),
            )
            
            connection.commit()
    
    
    @contextmanager
    def isolated_cache(self):
        """실험 실행 간 캐시 이점을 제거한다. 기존 SQLite 파일은 변경하지 않는다."""
        original_path = self._persistent_cache_path
        self.clear_cache()
        self._persistent_cache_path = None
        try:
            yield
        finally:
            self.clear_cache()
            self._persistent_cache_path = original_path

    def clear_cache(self) -> None:
        self._cached_count.cache_clear()
        
        
    def clear_persistent_cache(self) -> None:
        if self._persistent_cache_path is None:
            return
        
        with closing(sqlite3.connect(self._persistent_cache_path)) as connection:
            connection.execute(
                """
                DELETE FROM token_cache
                WHERE model = ?
                """,
                (self.model,),
            )
            
            connection.commit()
        
        
    def cache_info(self):
        return self._cached_count.cache_info()
    
    
    def stats(self) -> dict[str, int]:
        info = self.cache_info()
        
        return {
            "memory_hits": info.hits,
            "memory_misses": info.misses,
            "memory_currsize": info.currsize,
            "api_calls": self._api_calls,
            "persistent_hits": self._persistent_hits,
            "retries": self._retry_count,
        }
        
        
    def metadata(self) -> dict[str, Any]:
        return {
            "type": "gemini",
            "model": self.model,
            "method": "countTokens",
            "request_shape": "single_user_text",
            "memory_cache_size": self.cache_size,
            "max_retries": self.max_retries,
            "retry_base_seconds": self.retry_base_seconds,
            "persistent_cache": self._persistent_cache_path is not None,
            "stats": self.stats(),
        }


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