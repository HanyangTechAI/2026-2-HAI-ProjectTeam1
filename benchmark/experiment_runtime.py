from __future__ import annotations

import re

from functools import lru_cache
from pathlib import Path

from huggingface_hub import try_to_load_from_cache
from sentence_transformers import SentenceTransformer

from agent.agent import Agent
from agent.tools.email import EmailTool
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool

from .experiment_settings import EXPERIMENT_EMBEDDING_MODEL


_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    
    if _model is None:
        _model = SentenceTransformer(EXPERIMENT_EMBEDDING_MODEL)
        
    return _model


def _cached_model_revision(model_id: str) -> str | None:
    cached = try_to_load_from_cache(
        model_id,
        "config.json",
        revision="main",
    )
    
    if not isinstance(cached, str):
        return None
    
    path = Path(cached)
    parts = path.parts
    
    try:
        snapshot_index = parts.index("snapshots")
    except ValueError:
        return None
    
    if snapshot_index + 1 >= len(parts):
        return None
    
    revision = parts[snapshot_index + 1]
    
    if not re.fullmatch(
        r"[0-9a-fA-F]{40,64}",
        revision,
    ):
        return None
    
    return revision.lower()


def embedding_runtime_metadata() -> dict[str, object]:
    _get_model()
    
    revision = _cached_model_revision(EXPERIMENT_EMBEDDING_MODEL)
    
    if revision is None:
        raise RuntimeError("could not determine the resolved Hugging Face revision for the frozen embedding model")
    
    return {
        "model": EXPERIMENT_EMBEDDING_MODEL,
        
        "requested_revision": "main",
        
        "resolved_revision": revision,
        
        "revision_source": "huggingface_cache_snapshot",
        
        "normalize_embeddings": True,
        
        "preloaded_before_run_timing": True,
    }


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
