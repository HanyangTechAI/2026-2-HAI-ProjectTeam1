"""Seeded, offline natural-language filler; no task facts or API calls.

Each stream is fixed by sample identity and purpose. Length fitting reads a
prefix of that stream instead of drawing fresh randomness on every probe.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
import hashlib
import random
from typing import Any


_TOPICS = (
    ("회의 자료를 살펴보는 동안", ("표의 제목", "설명 순서", "문단 구성", "항목 배치", "여백")),
    ("발표 구성을 논의하는 자리에서", ("도입부", "예시 설명", "화면 구성", "마무리 문장", "발표 흐름")),
    ("안내문을 함께 읽어 보면서", ("문장 길이", "용어 설명", "제목 표현", "안내 순서", "문단 간격")),
    ("작업 노트를 정리하는 과정에서", ("메모 배치", "항목 이름", "분류 방식", "설명 분량", "강조 표시")),
    ("화면 시안을 검토하는 동안", ("색상 대비", "글자 크기", "버튼 배치", "메뉴 구성", "아이콘 모양")),
    ("설문 문항을 읽어 보는 자리에서", ("질문 순서", "선택지 표현", "질문 길이", "설명 문구", "응답 흐름")),
    ("교육 자료를 함께 살펴보며", ("예제 구성", "그림 배치", "연습 문제", "개념 설명", "학습 순서")),
    ("행사 안내를 준비하는 과정에서", ("인사말", "안내 문구", "목차 구성", "좌석 안내", "소개 문장")),
)
_ADVERBS = ("차근차근", "간단히", "함께", "다시 한번", "꼼꼼하게", "천천히")
_ACTIONS = ("살펴보았습니다", "검토했습니다", "정리했습니다", "비교해 보았습니다", "확인했습니다")
_OPINIONS = ("조금 길다는", "알아보기 쉽다는", "한층 간결해졌다는", "다소 복잡하다는", "자연스럽다는")


@dataclass
class _Stream:
    rng: random.Random
    words: list[str] = field(default_factory=list)
    sentences: list[str] = field(default_factory=list)
    starts: list[int] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)


class HorizonFiller:
    """Mode 2 composes sentences; mode 3 alternates repeated/natural sentences.

    The 50:50 unit is a pair of filler sentences, not model tokens. A final
    sentence can be partial because the existing fitter requests word counts.
    """

    def __init__(self, sample_id: str, mode: int, repeated_text: str) -> None:
        if type(mode) is not int or mode not in (2, 3):
            raise ValueError("HorizonFiller supports modes 2 and 3")
        self.sample_id = sample_id
        self.mode = mode
        self.repeated_sentences = tuple(
            sentence.strip() + "." for sentence in repeated_text.rstrip(".").split(". ")
        )
        self._streams: dict[str, _Stream] = {}

    def _stream(self, key: str) -> _Stream:
        if key not in self._streams:
            digest = hashlib.sha256(f"horizon_filler_v1:{self.sample_id}:{key}".encode()).digest()
            self._streams[key] = _Stream(random.Random(int.from_bytes(digest, "big")))
        return self._streams[key]

    @staticmethod
    def _natural_sentence(rng: random.Random) -> str:
        opening, aspects = rng.choice(_TOPICS)
        aspect = rng.choice(aspects)
        variant = rng.randrange(4)
        if variant == 0:
            return f"{opening} {aspect} 부분을 {rng.choice(_ADVERBS)} {rng.choice(_ACTIONS)}."
        if variant == 1:
            return f"{opening} {aspect}에 관한 의견을 {rng.choice(_ADVERBS)} 나누었습니다."
        if variant == 2:
            return f"{opening} {aspect} 부분이 {rng.choice(_OPINIONS)} 의견이 나왔습니다."
        return f"{opening} {aspect} 부분부터 {rng.choice(_ADVERBS)} 살펴보자는 의견이 나왔습니다."

    def _append(self, stream: _Stream) -> None:
        index = len(stream.sentences)
        repeated = self.mode == 3 and index % 2 == 0
        sentence = (self.repeated_sentences[(index // 2) % len(self.repeated_sentences)]
                    if repeated else self._natural_sentence(stream.rng))
        stream.starts.append(len(stream.words))
        stream.kinds.append("repeated" if repeated else "random_natural")
        stream.sentences.append(sentence)
        stream.words.extend(sentence.split())

    def words(self, length: int, *, key: str) -> str:
        stream = self._stream(key)
        while len(stream.words) < length:
            self._append(stream)
        return " ".join(stream.words[:length])

    def sentence(self, *, key: str, index: int = 0) -> str:
        stream = self._stream(key)
        while len(stream.sentences) <= index:
            self._append(stream)
        return stream.sentences[index]

    def counts(self, length: int, *, key: str) -> dict[str, int]:
        """Count used sentence slots, including a possible partial final one."""
        self.words(length, key=key)
        stream = self._stream(key)
        used = stream.kinds[:bisect_left(stream.starts, length)]
        return {kind: used.count(kind) for kind in ("repeated", "random_natural")}


class LLMHorizonFiller:
    """Read ordered, append-only LLM text; never replace it with local filler.

    Memory streams use stable offsets. History uses one ordered stream so
    adjacent turns retain the context supplied by the generation model.
    """

    def __init__(self, sample_id: str, mode: int, repeated_text: str, source: Any) -> None:
        if type(mode) is not int or mode not in (4, 5):
            raise ValueError("LLMHorizonFiller supports modes 4 and 5")
        self.sample_id = sample_id
        self.mode = mode
        self.source = source
        self.repeated_sentences = tuple(
            sentence.strip() + "." for sentence in repeated_text.rstrip(".").split(". ")
        )
        self._natural: tuple[str, ...] = ()
        self._natural_word_count = 0
        self._used_natural_count = 0
        self._streams: dict[str, _Stream] = {}

    def _natural_at(self, index: int) -> str:
        while index >= len(self._natural):
            sentences = tuple(self.source.prepare(
                self.sample_id, target_words=max(256, self._natural_word_count + 256),
            ))
            if (len(sentences) <= len(self._natural)
                    or sentences[:len(self._natural)] != self._natural
                    or any(not isinstance(sentence, str) or not sentence.strip() for sentence in sentences)):
                raise ValueError("LLM source must extend a fixed, nonempty sentence prefix")
            self._natural = sentences
            self._natural_word_count = sum(len(sentence.split()) for sentence in sentences)
        self._used_natural_count = max(self._used_natural_count, index + 1)
        return self._natural[index]

    def _stream(self, key: str) -> _Stream:
        if key not in self._streams:
            self._streams[key] = _Stream(random.Random(0))
        return self._streams[key]

    def _append(self, key: str, stream: _Stream) -> None:
        index = len(stream.sentences)
        repeated = self.mode == 5 and index % 2 == 0
        natural_index = index // 2 if self.mode == 5 else index
        # History starts at the beginning and stays in order. Other streams
        # keep a small, stable offset independent of how far the cache grows.
        offset = (0 if key == "history" else
                  int.from_bytes(hashlib.sha256(key.encode()).digest()[:2], "big") % 16)
        sentence = (self.repeated_sentences[(index // 2) % len(self.repeated_sentences)]
                    if repeated else self._natural_at(offset + natural_index))
        stream.starts.append(len(stream.words))
        stream.kinds.append("repeated" if repeated else "random_natural")
        stream.sentences.append(sentence)
        stream.words.extend(sentence.split())

    def words(self, length: int, *, key: str) -> str:
        stream = self._stream(key)
        while len(stream.words) < length:
            self._append(key, stream)
        return " ".join(stream.words[:length])

    def sentence(self, *, key: str, index: int = 0) -> str:
        stream = self._stream(key)
        while len(stream.sentences) <= index:
            self._append(key, stream)
        return stream.sentences[index]

    def counts(self, length: int, *, key: str) -> dict[str, int]:
        self.words(length, key=key)
        stream = self._stream(key)
        used = stream.kinds[:bisect_left(stream.starts, length)]
        return {kind: used.count(kind) for kind in ("repeated", "random_natural")}

    def history_segments(self, length: int, *, turn_count: int) -> list[str]:
        """Distribute an ordered prefix at sentence boundaries over past turns.

        Every turn receives at least one sentence. The fitter can add words to
        this minimum; only the final sentence of the entire prefix is partial.
        """
        self.sentence(key="history", index=turn_count - 1)
        stream = self._stream("history")
        minimum = sum(len(sentence.split()) for sentence in stream.sentences[:turn_count])
        total = minimum + length
        self.words(total, key="history")
        used = bisect_left(stream.starts, total)
        sentences = stream.sentences[:used]
        sentences[-1] = " ".join(stream.words[stream.starts[used - 1]:total])
        per_turn, extra = divmod(len(sentences), turn_count)
        result = []
        position = 0
        for index in range(turn_count):
            count = per_turn + (index < extra)
            result.append(" ".join(sentences[position:position + count]))
            position += count
        return result

    def provenance(self) -> dict[str, Any]:
        result = dict(self.source.provenance(self.sample_id))
        used = self._natural[:self._used_natural_count]
        result.update(
            used_sentence_count=len(used),
            used_prefix_sha256=hashlib.sha256("\n".join(used).encode()).hexdigest(),
        )
        return result
