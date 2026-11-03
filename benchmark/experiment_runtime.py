from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from agent.agent import Agent
from agent.tools.email import EmailTool
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool


_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    
    if _model is None:
        _model = SentenceTransformer(_MODEL_NAME)
        
    return _model


@lru_cache(maxsize=20000)
def multilingual_embedder(text: str) -> tuple[float, ...]:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    
    vector = _get_model().encode(
        text,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    
    return tuple(
        float(value)
        for value in vector
    )
    
    
def completed_action(
    response,
    calls,
    results,
) -> bool:
    return bool(
        response and calls and results and all(
            result.success
            for result in results
        )
    )
    
    
def environment_agent(environmet) -> Agent:
    return Agent(
        executor=ToolExecutor(
            file=FileTool(environmet.get("files", {})),
            email=EmailTool(environmet.get("valid_approval_ids", [])),
        ),
        goal_checker=completed_action,
    )