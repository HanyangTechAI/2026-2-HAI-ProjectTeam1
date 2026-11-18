from __future__ import annotations

import json

from agent.agent import AgentAction
from agent.tools.executor import ToolExecutor


SIDE_EFFECT_ACTIONS = frozenset(
    {
        ("email", "send_email"),
        ("email", "request_approval"),
        ("email", "draft_email"),
        ("calendar", "create_event"),
        ("task", "update_task"),
        ("file", "select_file"),
        ("file", "delete_file"),
    }
)


class RunSideEffectLedger:
    def __init__(self, executor: ToolExecutor) -> None:
        if not isinstance(executor, ToolExecutor):
            raise TypeError("executor must be a ToolExecutor")
        
        if len(executor.calls) != len(executor.results):
            raise RuntimeError("executor history is inconsistent")
        
        self.executor = executor
        
        self._cursor = len(executor.calls)
        
        self._completed: set[tuple[str, str, str]] = set()
        
    @staticmethod
    def _key(
        tool_name: str | None,
        action: str | None,
        arguments: dict,
    ) -> tuple[str, str, str] | None:
        if (tool_name, action) not in SIDE_EFFECT_ACTIONS:
            return None
        
        if (tool_name, action) == ("email", "send_email"):
            return (
                "email",
                "send_email",
                "once_per_task_run",
            )
            
        serialized = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        
        return (
            tool_name,
            action,
            serialized,
        )
        
    def _refresh(self) -> None:
        calls = self.executor.calls
        results = self.executor.results
        
        if len(calls) != len(results):
            raise RuntimeError("ToolExecutor call/result mismatch")
        
        if len(calls) < self._cursor:
            raise RuntimeError("ToolExecutor history was reset during run")
        
        for index in range(self._cursor, len(calls)):
            call = calls[index]
            result = results[index]
            
            if call.tool_name != result.tool_name or call.action != result.action:
                raise RuntimeError("ToolExecutor action/result mismatch")
            
            if result.success is not True:
                continue
            
            key = self._key(
                call.tool_name,
                call.action,
                call.arguments,
            )
            
            if key is not None:
                self._completed.add(key)
                
        self._cursor = len(calls)
        
    def already_completed(
        self,
        action: AgentAction,
    ) -> bool:
        key = self._key(
            action.tool_name,
            action.action,
            action.arguments,
        )
        
        if key is None:
            return False
        
        self._refresh()
        
        return key in self._completed