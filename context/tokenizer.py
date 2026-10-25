from __future__ import annotations

from collections.abc import Callable


TokenCounter = Callable[[str], int]


def provisional_token_count(text: str) -> int:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    
    if not text:
        return 0
    
    return len(text.split())


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