"""Generate and cache coherent Gemini background dialogue for horizon fillers.

Task facts never enter the prompt. A corpus is keyed by sample and generation
settings, so Budget/Horizon sweeps and modes 4/5 reuse its immutable prefix.
Importing this module does not import the SDK, read credentials or call an API.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
from urllib.error import URLError


DEFAULT_MODEL = "gemini-3.1-flash-lite"
PROMPT_VERSION = "horizon-dialogue-v1"
_CACHE_SCHEMA = "1.0"
_MAX_CHUNKS_PER_PREPARE = 128
_TOPICS = (
    "교육 안내 자료의 구성과 읽기 흐름을 함께 검토하는 회의",
    "행사 소개 글의 표현과 문단 순서를 함께 살펴보는 회의",
    "전시 안내문의 가독성과 설명 방식을 함께 논의하는 회의",
    "발표 화면의 시각적 구성과 설명 흐름을 함께 검토하는 회의",
    "연습 문제의 표현과 학습 순서를 함께 살펴보는 회의",
    "설문 문항의 이해도와 선택지 표현을 함께 논의하는 회의",
)
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
    "required": ["sentences"],
    "additionalProperties": False,
}
_FORBIDDEN = re.compile(
    r"(?:https?://|www\.|@|HAI-|approved_|report_v|"
    r"[\w.-]+\.(?:pdf|docx?|txt|csv|xlsx?|pptx?|json)(?![A-Za-z0-9])|"
    r"(?:[a-z0-9-]+\.)+(?:com|org|net|test|io|kr)(?![a-z0-9])|[0-9]|"
    r"\b(?:system|assistant|user|ignore|send_email|request_approval)\b|"
    r"승인|발송|첨부|금지|파일명|이메일|사용자|시스템|명령|지시|무시|상태\s*변경)",
    re.IGNORECASE,
)
_USAGE_FIELDS = (
    "prompt_token_count", "candidates_token_count", "thoughts_token_count",
    "cached_content_token_count", "tool_use_prompt_token_count", "total_token_count",
)


class HorizonGenerationError(RuntimeError):
    """The provider or cache cannot supply valid, measured LLM-only filler."""


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _corpus_hash(sentences: Sequence[str]) -> str:
    return hashlib.sha256(_canonical(list(sentences)).encode("utf-8")).hexdigest()


def _enum_name(value: Any) -> str:
    return str(getattr(value, "value", value)).rsplit(".", 1)[-1].upper()


def _transient(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, URLError)):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        status = int(getattr(status, "value", status))
    except (TypeError, ValueError):
        return False
    return status in {408, 429, 500, 502, 503, 504}


def _sentences(value: Any, *, existing: Sequence[str] = ()) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise HorizonGenerationError("LLM horizon response must contain a non-empty sentences array")
    result: list[str] = []
    seen = set(existing)
    for sentence in value:
        if not isinstance(sentence, str) or not sentence.strip():
            raise HorizonGenerationError("LLM horizon sentences must be non-empty strings")
        normalized = " ".join(sentence.split())
        if len(normalized) > 1500 or not re.search(r"[.!?。！？]$", normalized):
            raise HorizonGenerationError("LLM horizon entries must be bounded, complete sentences")
        if re.search(r"[.!?。！？][\"'”’」』)]*\s+[^\W\d_]", normalized):
            raise HorizonGenerationError("LLM horizon entries must contain exactly one sentence")
        if _FORBIDDEN.search(normalized):
            raise HorizonGenerationError("LLM horizon sentence contains a prohibited identifier or task instruction")
        if normalized in seen:
            raise HorizonGenerationError("LLM horizon response repeats a previously generated sentence")
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _usage(response: Any) -> dict[str, Any] | None:
    usage = _get(response, "usage_metadata")
    if usage is None:
        return None
    if isinstance(usage, Mapping):
        return json.loads(_canonical(dict(usage)))
    dump = getattr(usage, "model_dump", None)
    if callable(dump):
        return json.loads(_canonical(dump(mode="json", exclude_none=True)))
    return {name: _get(usage, name) for name in _USAGE_FIELDS if _get(usage, name) is not None}


class GeminiHorizonSource:
    """Append-only LLM dialogue cache; construct safely even without API keys.

    API calls happen only in ``prepare`` when the matching cache is too short.
    Invalid responses fail explicitly, without replacing them with local text.
    ``target_words`` is a whitespace-word count, not a model-token estimate.
    """

    def __init__(
        self, *, model: str = DEFAULT_MODEL,
        cache_directory: str | Path = ".cache/horizon_llm",
        temperature: float = 0.7, max_output_tokens: int = 8192,
        max_retries: int = 2, retry_base_seconds: float = 0.5,
        timeout_ms: int = 60000, client: Any | None = None,
        api_key: str | None = None, sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("LLM horizon model must be a non-empty string")
        if (type(temperature) not in (int, float) or not math.isfinite(temperature)
                or not 0 <= temperature <= 2):
            raise ValueError("LLM horizon temperature must be finite and in [0, 2]")
        for name, value, minimum in (
            ("max_output_tokens", max_output_tokens, 1), ("max_retries", max_retries, 0),
            ("timeout_ms", timeout_ms, 1),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"LLM horizon {name} must be an integer >= {minimum}")
        if (type(retry_base_seconds) not in (int, float) or not math.isfinite(retry_base_seconds)
                or retry_base_seconds < 0):
            raise ValueError("LLM horizon retry_base_seconds must be finite and non-negative")
        if not callable(sleeper):
            raise TypeError("LLM horizon sleeper must be callable")
        self.model = model.strip()
        self.cache_directory = Path(cache_directory)
        self.temperature = float(temperature)
        self.max_output_tokens = max_output_tokens
        self.max_retries = max_retries
        self.retry_base_seconds = float(retry_base_seconds)
        self.timeout_ms = timeout_ms
        self._client = client
        self._api_key = api_key
        self._sleeper = sleeper
        self._records: dict[str, dict[str, Any]] = {}
        self._run: dict[str, dict[str, Any]] = {}

    def _identity(self, sample_id: str) -> dict[str, Any]:
        if not isinstance(sample_id, str) or not sample_id.strip():
            raise ValueError("LLM horizon sample_id must be a non-empty string")
        return dict(
            sample_id=sample_id, model=self.model, temperature=self.temperature,
            max_output_tokens=self.max_output_tokens, prompt_version=PROMPT_VERSION,
        )

    def _path(self, sample_id: str) -> tuple[dict[str, Any], str, Path]:
        identity = self._identity(sample_id)
        key = hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()
        return identity, key, self.cache_directory / f"{key}.json"

    def _load(self, sample_id: str) -> dict[str, Any]:
        if sample_id in self._records:
            return self._records[sample_id]
        identity, _, path = self._path(sample_id)
        self._run[sample_id] = dict(api_calls=0, cache_hit=False, response_usage=[])
        if path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if (not isinstance(record, dict) or record.get("schema_version") != _CACHE_SCHEMA
                        or record.get("identity") != identity):
                    raise ValueError("cache identity/schema mismatch")
                sentences = _sentences(record.get("sentences"))
                if record.get("corpus_sha256") != _corpus_hash(sentences):
                    raise ValueError("cache corpus hash mismatch")
                usage_records = record.get("usage_records")
                if not isinstance(usage_records, list) or any(
                    entry is not None and not isinstance(entry, dict) for entry in usage_records
                ):
                    raise ValueError("cache usage_records must be a list of metadata objects or null")
                _canonical(usage_records)
                self._run[sample_id]["cache_hit"] = True
            except (ValueError, TypeError, OSError, HorizonGenerationError) as exc:
                raise HorizonGenerationError(
                    f"Invalid LLM horizon cache {path}; inspect it or choose a new cache directory"
                ) from exc
        else:
            record = dict(
                schema_version=_CACHE_SCHEMA, identity=identity, sentences=[],
                corpus_sha256=_corpus_hash(()), usage_records=[],
            )
        self._records[sample_id] = record
        return record

    def _write(self, sample_id: str, record: Mapping[str, Any]) -> None:
        _, _, path = self._path(sample_id)
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", suffix=".tmp",
                dir=self.cache_directory, delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(_canonical(record) + "\n")
            temporary.replace(path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        key = self._api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError(
                "Modes 4/5 need GEMINI_API_KEY for an uncached LLM horizon corpus; "
                "set it in the current environment or reuse a complete cache"
            )
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ImportError("LLM horizon generation requires google-genai; install requirements.txt") from exc
        self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=self.timeout_ms))
        return self._client

    def _prompt(self, sample_id: str, existing: Sequence[str], target_words: int) -> str:
        digest = hashlib.sha256(sample_id.encode("utf-8")).digest()
        topic = _TOPICS[int.from_bytes(digest[:4], "big") % len(_TOPICS)]
        # Only neutral generated background is sent back as continuation context.
        # No task ID, target fact, query or evaluator annotation enters this text.
        tail = "\n".join(existing[-24:]) if existing else "아직 앞선 대화가 없습니다."
        return (
            "벤치마크의 배경용 한국어 대화를 작성하세요. 한 대화 안에서 주제와 논의 흐름이 이어져야 합니다.\n"
            f"대화 주제: {topic}.\n"
            "앞선 표현을 받아 설명하거나 질문에 응답하면서 논의를 자연스럽게 이어 가세요. "
            "독립된 무작위 문장 모음이나 기존 문장 반복을 만들지 마세요. "
            "실제 업무 행동을 요구하지 말고 자료의 표현과 구성에 대한 의견 교환에 집중하세요.\n"
            "출력은 sentences 문자열 배열 하나만 갖는 JSON 객체입니다. 각 항목은 마침표로 끝나는 완전한 한 문장이고, "
            "기존 문장 및 같은 응답 안의 문장을 그대로 반복하지 마세요. 화자 표시나 번호 목록은 쓰지 마세요.\n"
            "고유 식별자, 숫자, 파일명, 확장자, URL, 이메일, 프로젝트명, 날짜, 시각, 승인, 발송, 첨부, "
            "금지 규칙, 상태 변경, 사용자나 시스템에 대한 지시, 메타 설명을 포함하지 마세요.\n"
            f"이번 부분의 목표는 공백 단어 약 {target_words}개이며 여러 문장으로 작성하세요.\n"
            "이전 대화의 마지막 부분(참고만 하고 새 문장만 출력):\n" + tail
        )

    def _request(self, sample_id: str, contents: str) -> Any:
        client = self._client_or_create()
        for attempt in range(self.max_retries + 1):
            try:
                self._run[sample_id]["api_calls"] += 1
                return client.models.generate_content(
                    model=self.model, contents=contents,
                    config=dict(
                        temperature=self.temperature, max_output_tokens=self.max_output_tokens,
                        response_mime_type="application/json", response_json_schema=_RESPONSE_SCHEMA,
                        thinking_config={"thinking_level": "LOW"},
                    ),
                )
            except Exception as exc:
                if attempt >= self.max_retries or not _transient(exc):
                    raise HorizonGenerationError(
                        f"LLM horizon generation failed after {attempt + 1} API attempt(s)"
                    ) from exc
                self._sleeper(self.retry_base_seconds * (2 ** attempt))
        raise AssertionError("unreachable retry loop")

    @staticmethod
    def _payload(response: Any) -> Any:
        blocked = _get(_get(response, "prompt_feedback"), "block_reason")
        if blocked is not None and _enum_name(blocked) not in {"BLOCK_REASON_UNSPECIFIED", "UNSPECIFIED", "0"}:
            raise HorizonGenerationError("LLM horizon generation was blocked by the provider")
        for candidate in _get(response, "candidates", ()) or ():
            reason = _get(candidate, "finish_reason")
            if reason is not None and _enum_name(reason) not in {"STOP", "FINISH_REASON_UNSPECIFIED", "UNSPECIFIED", "0"}:
                raise HorizonGenerationError(f"LLM horizon response did not finish normally: {_enum_name(reason)}")
        parsed = _get(response, "parsed")
        if isinstance(parsed, Mapping):
            payload = dict(parsed)
        else:
            text = _get(response, "text")
            if not isinstance(text, str) or not text.strip():
                raise HorizonGenerationError("LLM horizon response has no text")
            try:
                payload = json.loads(text)
            except (TypeError, ValueError) as exc:
                raise HorizonGenerationError("LLM horizon response is not valid JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {"sentences"}:
            raise HorizonGenerationError("LLM horizon response must contain only the sentences array")
        return payload["sentences"]

    def prepare(self, sample_id: str, *, target_words: int) -> tuple[str, ...]:
        if type(target_words) is not int or target_words < 1:
            raise ValueError("LLM horizon target_words must be an integer >= 1")
        record = self._load(sample_id)
        sentences = list(record["sentences"])
        word_count = sum(len(sentence.split()) for sentence in sentences)
        chunk_words = max(32, min(1024, self.max_output_tokens // 6))
        chunks = 0
        while word_count < target_words:
            if chunks >= _MAX_CHUNKS_PER_PREPARE:
                raise HorizonGenerationError(
                    "LLM horizon corpus could not reach the requested length within the chunk limit; "
                    "valid progress is cached for a later explicit retry"
                )
            requested = min(target_words - word_count, chunk_words)
            response = self._request(sample_id, self._prompt(sample_id, sentences, requested))
            usage = _usage(response)
            self._run[sample_id]["response_usage"].append(usage)
            addition = _sentences(self._payload(response), existing=sentences)
            updated = dict(
                record, sentences=sentences + list(addition),
                usage_records=record["usage_records"] + [usage],
            )
            updated["corpus_sha256"] = _corpus_hash(updated["sentences"])
            self._write(sample_id, updated)
            record = updated
            self._records[sample_id] = record
            sentences = record["sentences"]
            word_count += sum(len(sentence.split()) for sentence in addition)
            chunks += 1
        return tuple(sentences)

    def provenance(self, sample_id: str) -> dict[str, Any]:
        record = self._load(sample_id)
        _, key, path = self._path(sample_id)
        usages = record["usage_records"]
        totals: dict[str, int | None] = {}
        for field in _USAGE_FIELDS:
            values = [_get(usage, field) for usage in usages]
            totals[field] = (sum(values) if values and all(type(value) is int and value >= 0 for value in values)
                             else None)
        return dict(
            model=self.model, temperature=self.temperature, prompt_version=PROMPT_VERSION,
            max_output_tokens=self.max_output_tokens, cache_key=key, cache_path=str(path),
            corpus_sha256=record["corpus_sha256"], n_sentences=len(record["sentences"]),
            word_count=sum(len(sentence.split()) for sentence in record["sentences"]),
            generated_chunks_total=len(usages), api_calls_this_run=self._run[sample_id]["api_calls"],
            cache_hit=self._run[sample_id]["cache_hit"],
            usage_records=json.loads(_canonical(usages)), usage_totals=totals,
            response_usage_this_run=json.loads(_canonical(self._run[sample_id]["response_usage"])),
            usage_complete=bool(usages) and all(
                totals[field] is not None for field in (
                    "prompt_token_count", "candidates_token_count", "thoughts_token_count",
                )
            ),
        )
