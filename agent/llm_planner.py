"""완성된 context를 Gemini 3.1 Flash-Lite structured output으로 읽고 AgentAction으로 바꾼다.

프롬프트를 조립하지 않는다. 토큰을 세지 않고, 툴을 실행하지 않으며, 저장소에
쓰지 않는다. Agent는 규칙 Planner 대신 이 객체의 plan을 그대로 호출한다.
"""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from time import perf_counter

from .agent import AgentAction
from .tools.calendar import CalendarTool
from .tools.email import EmailTool
from .tools.file import FileTool
from .tools.task import TaskTool

DEFAULT_MODEL = "gemini-3.1-flash-lite"
CLIENT_TIMEOUT_MS = 60_000
ARGUMENT_KEYS = (
    "recipient",
    "subject",
    "body",
    "attachment",
    "approval_id",
    "filename",
    "start_time",
    "end_time",
    "title",
    "query",
    "task_id",
    "status",
    "assignee",
    "owner",
)
TOOL_METHODS = {
    ("email", "draft_email"): EmailTool.draft_email,
    ("email", "request_approval"): EmailTool.request_approval,
    ("email", "send_email"): EmailTool.send_email,
    ("file", "find_file"): FileTool.find_file,
    ("file", "select_file"): FileTool.select_file,
    ("file", "delete_file"): FileTool.delete_file,
    ("calendar", "create_event"): CalendarTool.create_event,
    ("calendar", "find_event"): CalendarTool.find_event,
    ("task", "create_task"): TaskTool.create_task,
    ("task", "update_task"): TaskTool.update_task,
}
ACTION_TO_TOOL = {action: tool_name for (tool_name, action) in TOOL_METHODS}
TOOL_NAMES = frozenset({"email", "file", "calendar", "task"})
_TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})

class LLMProviderError(RuntimeError):
    def __init__(
        self,
        *,
        model: str,
        attempts: int,
        cause: Exception,
    ) -> None:
        self.model = model
        self.attempts = attempts
        self.error_type = type(cause).__name__
        
        status = getattr(cause, "status_code", None)
        
        if status is None:
            status = getattr(cause, "code", None)
            
        self.status_code = status
        
        super().__init__(f"LLM provider request failed: model={model}, attempts={attempts}, error_type={self.error_type}, status_code={status}")
        

SYSTEM_INSTRUCTION = """\
너는 Context Builder가 만든 context를 읽고 행동 하나를 고른다.
context에 적힌 [PROTECTED CONSTRAINTS]를 지킨다.
[RELEVANT MEMORY]의 과거 툴 결과는 이번 [CURRENT TASK]를 이미 끝낸 증거가 아니다.
[RECENT TOOL RESULTS]는 이번 과제에서 방금 실행한 결과다. [CURRENT TASK] 아래에 있다.
파일을 찾거나 메일을 보내는 [CURRENT TASK]는 반드시 도구를 부른다. 파일 이름을 텍스트로만 말하고 끝내지 않는다.
file.find_file, file.select_file, file.delete_file은 filename을 반드시 채운다. 빈 문자열은 안 된다.
filename은 context에 적힌 실제 파일 이름만 쓴다. 예: e3a45ecc07_report_v3.pdf.
email.request_approval과 email.send_email은 recipient를 반드시 채운다.
email.request_approval의 성공은 접수다. output.kind=receipt, status=pending, authorized_to_send=false이면 approval_id는 접수번호다. 발송 허가가 아니다. 그 번호로 send_email을 부르지 말고 text_response에 접수만 끝났다고 적는다.
send_email의 approval_id에는 output.kind=grant, status=granted, authorized_to_send=true인 번호만 쓴다. 예: approved_787092556935. approval_001 같은 접수번호를 허가증처럼 쓰지 않는다.
file.find_file이 success=true이고 output.found=true이면 파일을 이미 찾은 것이다. 같은 find_file을 다시 부르지 말고 text_response에 파일 이름을 적는다.
email.send_email이 success=true이면 이미 보낸 것이다. 다시 보내지 말고 끝낸다. approved=false이거나 authorized_to_send=false이면 허가된 발송이 아니다.
같은 도구를 같은 인자로 반복하지 않는다.
context에 없는 메일 주소, 파일 이름, 승인 번호, 시각, 담당자를 만들지 않는다.
도구를 부르면 tool_name과 action을 채우고, 필요한 arguments만 채운다.
도구를 부르지 않으면 tool_name과 action은 빈 문자열이고 text_response에 이유를 적는다.
행동 하나만 고른다.
"""

ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_name": {
            "type": "string",
            "enum": ["", "email", "file", "calendar", "task"],
            "description": "도구를 부르지 않으면 빈 문자열.",
        },
        "action": {
            "type": "string",
            "description": (
                "email: draft_email, request_approval, send_email. "
                "file: find_file, select_file, delete_file. "
                "calendar: create_event, find_event. "
                "task: create_task, update_task. "
                "파일을 찾으면 반드시 find_file. 도구를 부르지 않으면 빈 문자열."
            ),
        },
        "arguments": {
            "type": "object",
            "description": "이번 호출에 쓰는 인자만 채운다. 쓰지 않는 인자는 빈 문자열.",
            "properties": {
                "recipient": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "attachment": {"type": "string"},
                "approval_id": {
                    "type": "string",
                    "description": (
                        "승인 완료 번호만. kind=grant, status=granted인 값. "
                        "request_approval이 준 접수번호(kind=receipt, status=pending)는 금지."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": "find_file, select_file, delete_file에 필수. 빈 문자열 금지.",
                },
                "start_time": {"type": "string", "description": "ISO-8601 시각."},
                "end_time": {"type": "string"},
                "title": {"type": "string"},
                "query": {"type": "string"},
                "task_id": {"type": "string"},
                "status": {"type": "string"},
                "assignee": {"type": "string"},
                "owner": {"type": "string"},
            },
        },
        "text_response": {
            "type": "string",
            "description": "도구를 부르지 않을 때의 응답. 도구를 부르면 빈 문자열.",
        },
    },
    "required": ["tool_name", "action", "arguments", "text_response"],
}


GEMINI_31_FLASH_LITE_INPUT_USD_PER_MILLION = 0.25
GEMINI_31_FLASH_LITE_OUTPUT_USD_PER_MILLION = 1.50
GEMINI_31_FLASH_LITE_CACHED_INPUT_USD_PER_MILLION = 0.025


@dataclass(frozen=True)
class PlannerTelemetry:
    planner_calls: int
    api_calls: int
    usage_metadata_calls: int
    
    prompt_tokens: int | None
    prompt_tokens_per_call: tuple[int | None, ...]
    max_prompt_tokens: int | None
    
    cached_prompt_tokens: int | None
    tool_use_prompt_tokens: int | None
    
    candidate_tokens: int | None
    thoughts_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    
    planning_latency_ms: float
    estimated_cost_usd: float | None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "planner_calls": self.planner_calls,
            "api_calls": self.api_calls,
            "usage_metadata_calls": self.usage_metadata_calls,
            
            "prompt_tokens": self.prompt_tokens,
            "prompt_tokens_per_call": list(self.prompt_tokens_per_call),
            "max_prompt_tokens": self.max_prompt_tokens,
            
            "cached_prompt_tokens": self.cached_prompt_tokens,
            "tool_use_prompt_tokens": self.tool_use_prompt_tokens,
            
            "candidate_tokens": self.candidate_tokens,
            "thoughts_tokens": self.thoughts_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            
            "planning_latency_ms": self.planning_latency_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
            
            "pricing": {
                "currency": "USD",
                "provider": "google",
                "model": DEFAULT_MODEL,
                "tier": "developer_api_standard_paid",
                "input_per_million": GEMINI_31_FLASH_LITE_INPUT_USD_PER_MILLION,
                "cached_input_per_million": GEMINI_31_FLASH_LITE_CACHED_INPUT_USD_PER_MILLION,
                "output_per_million": GEMINI_31_FLASH_LITE_OUTPUT_USD_PER_MILLION,
                "output_includes_thinking": True,
            }
        }


class LLMPlanner:
    """Gemini 3.1 Flash-Lite의 JSON 응답을 AgentAction으로 바꾸는 계획기.

    temperature는 실험에서 전략마다 같은 값으로 고정하려고 설정에 넣고 API에도 보낸다.
    thinking_level은 LOW, MEDIUM, HIGH만 받으며 비용이 작은 LOW를 쓴다.
    호출 실패와 깨진 JSON, 이상한 인자는 예외로 실험을 멈추지 않고 텍스트 행동으로 끝낸다.
    API 키가 없거나 패키지가 없으면 그 설정 오류는 그대로 올린다.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        client: Any | None = None,
        api_key: str | None = None,
        temperature: float = 0,
        system_instruction: str = SYSTEM_INSTRUCTION,
        max_output_tokens: int = 512,
        thinking_level: str = "LOW",
        max_retries: int = 2,
        retry_base_seconds: float = 0.5,
        timeout_ms: int = CLIENT_TIMEOUT_MS,
        sleeper: Any | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= float(temperature) <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if not isinstance(system_instruction, str) or not system_instruction.strip():
            raise ValueError("system_instruction must be a non-empty string")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        if thinking_level not in {"LOW", "MEDIUM", "HIGH"}:
            raise ValueError("thinking_level must be LOW, MEDIUM, or HIGH")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        if (isinstance(retry_base_seconds, bool)
                or not isinstance(retry_base_seconds, (int, float))
                or retry_base_seconds < 0):
            raise ValueError("retry_base_seconds must be non-negative")
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms < 1:
            raise ValueError("timeout_ms must be a positive integer")
        if sleeper is not None and not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self.model = model.strip()
        self.temperature = float(temperature)
        self.system_instruction = system_instruction
        self.max_output_tokens = max_output_tokens
        self.thinking_level = thinking_level
        self.max_retries = max_retries
        self.retry_base_seconds = float(retry_base_seconds)
        self.timeout_ms = timeout_ms
        self._sleeper = sleeper or _sleep
        self._client = client
        self._api_key = api_key
        
        self._planner_calls = 0
        self._api_calls = 0
        self._usage_metadata_calls = 0
        
        self._prompt_tokens = 0
        self._prompt_tokens_per_call: list[int | None] = []
        
        self._cached_prompt_tokens = 0
        self._tool_use_prompt_tokens = 0
        
        self._cadidate_tokens = 0
        self._thoughts_tokens = 0
        self._total_tokens = 0
        
        self._planning_latency_ms = 0.0

    def plan(self, context: str) -> AgentAction:
        """완성된 context를 모델에 넘기고 AgentAction 하나를 돌려준다."""
        if not isinstance(context, str) or not context.strip():
            return _text("context가 비어 있어 행동하지 않았습니다.")
        
        self._planner_calls += 1
        started = perf_counter()
        
        try:
            client = self._client_or_create()
            for attempt in range(self.max_retries + 1):
                try:
                    self._api_calls += 1
                    
                    response = client.models.generate_content(
                        model=self.model,
                        contents=context,
                        config=self.generation_config(),
                    )
                    
                except Exception as exc:
                    if attempt < self.max_retries and _transient(exc):
                        self._sleeper(self.retry_base_seconds * (2 ** attempt))
                        continue
                    raise LLMProviderError(
                        model=self.model,
                        attempts=attempt + 1,
                        cause=exc
                    ) from exc
                
                self._record_usage_metadata(response)
                
                try:
                    payload = _payload_from_response(response)
                except ValueError:
                    return _text("모델 응답을 행동으로 읽지 못했습니다.")
                
                return agent_action_from_model_output(payload)
            
            raise RuntimeError("LLM retry loop exited unexpectedly")
        
        finally:
            self._planning_latency_ms += (perf_counter() - started) * 1000.0

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ImportError(
                "google-genai is required for LLMPlanner. Install it with: pip install google-genai"
            ) from exc
        api_key = self._api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini API key is required. Pass api_key=... or set GEMINI_API_KEY.")
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=self.timeout_ms),
        )
        return self._client

    def generation_config(self) -> dict[str, Any]:
        """google-genai generate_content에 넘기는 설정. context는 contents로 따로 간다."""
        return {
            "system_instruction": self.system_instruction,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "response_mime_type": "application/json",
            "response_json_schema": ACTION_SCHEMA,
            "thinking_config": {"thinking_level": self.thinking_level},
        }
        
        
    def _record_usage_metadata(self, response: Any) -> None:
        usage = getattr(response, "usage_metadata", None)
        
        if usage is None:
            return
        
        self._usage_metadata_calls += 1
        
        prompt_tokens = getattr(
            usage,
            "prompt_token_count",
            None,
        )
        
        if type(prompt_tokens) is int and prompt_tokens >= 0:
            self._prompt_tokens += prompt_tokens
            self._prompt_tokens_per_call.append(prompt_tokens)
        else:
            self._prompt_tokens_per_call.append(None)
        
        self._cached_prompt_tokens += _usage_int(usage, "cached_content_token_count")
        
        self._tool_use_prompt_tokens += _usage_int(usage, "tool_use_prompt_token_count")
        
        candidate_tokens = getattr(usage, "candidates_token_count", None)
        
        if candidate_tokens is None:
            candidate_tokens = getattr(usage, "response_token_count", None)
            
        if type(candidate_tokens) is int and candidate_tokens >= 0:
            self._cadidate_tokens += candidate_tokens
            
        self._thoughts_tokens += _usage_int(usage, "thoughts_token_count")
        
        total = getattr(usage, "total_token_count", None)
        
        if type(total) is int and total >= 0:
            self._total_tokens += total
        else:
            candidate = candidate_tokens if type(candidate_tokens) is int and candidate_tokens >= 0 else 0
            
            self._total_tokens += (
                _usage_int(usage, "prompt_token_count")
                + _usage_int(usage, "tool_use_prompt_token_count")
                + candidate
                + _usage_int(usage, "thoughts_token_count")
            )
            
            
    def telemetry(self) -> PlannerTelemetry:
        meatadata_available = self._usage_metadata_calls > 0
        
        if meatadata_available:
            output_tokens = self._cadidate_tokens + self._thoughts_tokens
            billable_intput_tokens = self._prompt_tokens + self._tool_use_prompt_tokens
            cached_tokens = min(self._cached_prompt_tokens, billable_intput_tokens)
            uncached_tokens = max(0, billable_intput_tokens - cached_tokens)
            
            estimated_cost_usd = (
                uncached_tokens * GEMINI_31_FLASH_LITE_INPUT_USD_PER_MILLION + cached_tokens * GEMINI_31_FLASH_LITE_CACHED_INPUT_USD_PER_MILLION + output_tokens * GEMINI_31_FLASH_LITE_OUTPUT_USD_PER_MILLION
                ) / 1_000_000.0
            
        elif self._api_calls == 0:
            output_tokens = 0
            estimated_cost_usd = 0.0
            
        else:
            output_tokens = None
            estimated_cost_usd = None
            
        valid_prompt_tokens = [
            value
            for value in self._prompt_tokens_per_call
            if value is not None
        ]
        
        max_prompt_tokens = max(valid_prompt_tokens) if valid_prompt_tokens else None
            
        return PlannerTelemetry(
            planner_calls=self._planner_calls,
            api_calls=self._api_calls,
            usage_metadata_calls=self._usage_metadata_calls,
            
            prompt_tokens=self._prompt_tokens if meatadata_available else None,
            prompt_tokens_per_call=tuple(self._prompt_tokens_per_call),
            max_prompt_tokens=max_prompt_tokens,
            
            cached_prompt_tokens=self._cached_prompt_tokens if meatadata_available else None,
            tool_use_prompt_tokens=self._tool_use_prompt_tokens if meatadata_available else None,
            
            candidate_tokens=self._cadidate_tokens if meatadata_available else None,
            thoughts_tokens=self._thoughts_tokens if meatadata_available else None,
            output_tokens=output_tokens,
            total_tokens=self._total_tokens if meatadata_available else None,
            
            planning_latency_ms=self._planning_latency_ms,
            estimated_cost_usd=estimated_cost_usd,
        )

def shared_system_instruction(system_prompt: str) -> str:
    """실험 시스템 프롬프트와 JSON 행동 규칙을 한 문자열로 붙인다.

    전략마다 따로 만들지 않는다. 빈 프롬프트면 행동 규칙만 쓴다.
    """
    if not isinstance(system_prompt, str):
        raise TypeError("system_prompt must be a string")
    prompt = system_prompt.strip()
    if not prompt:
        return SYSTEM_INSTRUCTION
    return f"{prompt}\n\n{SYSTEM_INSTRUCTION}"


@dataclass(frozen=True)
class LLMRunConfig:
    """한 실험의 모델, temperature, 시스템 지시문.

    전략 루프 밖에서 한 번 만들고, 모든 전략의 Planner가 이 값을 복사해 쓴다.
    """

    model: str = DEFAULT_MODEL
    temperature: float = 0
    system_instruction: str = SYSTEM_INSTRUCTION
    max_output_tokens: int = 512
    thinking_level: str = "LOW"
    max_retries: int = 2
    retry_base_seconds: float = 0.5
    timeout_ms: int = CLIENT_TIMEOUT_MS

    def __post_init__(self) -> None:
        checked = LLMPlanner(
            model=self.model,
            temperature=self.temperature,
            system_instruction=self.system_instruction,
            max_output_tokens=self.max_output_tokens,
            thinking_level=self.thinking_level,
            max_retries=self.max_retries,
            retry_base_seconds=self.retry_base_seconds,
            timeout_ms=self.timeout_ms,
            client=object(),
        )
        object.__setattr__(self, "model", checked.model)
        object.__setattr__(self, "temperature", checked.temperature)
        object.__setattr__(self, "system_instruction", checked.system_instruction)

    def planner(self) -> LLMPlanner:
        """같은 모델, temperature, 시스템 지시문으로 Planner를 만든다."""
        return LLMPlanner(
            model=self.model,
            temperature=self.temperature,
            system_instruction=self.system_instruction,
            max_output_tokens=self.max_output_tokens,
            thinking_level=self.thinking_level,
            max_retries=self.max_retries,
            retry_base_seconds=self.retry_base_seconds,
            timeout_ms=self.timeout_ms,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": "google",
            "model": self.model,
            "temperature": self.temperature,
            "system_instruction": self.system_instruction,
            "max_output_tokens": self.max_output_tokens,
            "thinking_level": self.thinking_level,
            "max_retries": self.max_retries,
            "retry_base_seconds": self.retry_base_seconds,
            "timeout_ms": self.timeout_ms,
        }


def agent_action_from_model_output(payload: Any) -> AgentAction:
    """structured output 하나를 기존 AgentAction으로 바꾼다.

    도구 이름과 action이 둘 다 있으면 툴 호출이다. 둘 다 없거나 서로 맞지 않으면
    텍스트로 끝낸다. 빈 값, 잘못된 타입, 그 action이 받지 않는 인자는 뺀다.
    깨진 JSON도 예외 없이 텍스트 행동이 된다.
    """
    try:
        data = _as_object(payload)
    except ValueError:
        return _text("모델 응답을 행동으로 읽지 못했습니다.")
    tool_name, action, mismatch = _normalize_call(data.get("tool_name"), data.get("action"))
    if mismatch is not None:
        return _text(mismatch)
    arguments = _arguments(data.get("arguments"), tool_name, action)
    if tool_name is not None and action is not None:
        return AgentAction(tool_name, action, arguments, None)
    return _text(_blank(data.get("text_response")))


def _payload_from_response(response: Any) -> Mapping[str, Any] | str:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, Mapping):
        return parsed
    if parsed is not None and hasattr(parsed, "model_dump"):
        dumped = parsed.model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message is not None else None
        if isinstance(content, str) and content.strip():
            return content
    raise ValueError("Gemini response did not contain JSON")
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, Mapping):
        return parsed
    if parsed is not None and hasattr(parsed, "model_dump"):
        dumped = parsed.model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    raise ValueError("OpenAI response did not contain JSON")


def _as_object(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(_json_slice(payload))
        except json.JSONDecodeError as exc:
            raise ValueError("model response was not valid JSON") from exc
    if isinstance(payload, list):
        objects = [item for item in payload if isinstance(item, Mapping)]
        if len(objects) != 1:
            raise ValueError("model response JSON must be an object")
        payload = objects[0]
    if not isinstance(payload, Mapping):
        raise ValueError("model response JSON must be an object")
    return payload


def _normalize_call(tool_name: Any, action: Any) -> tuple[str | None, str | None, str | None]:
    tool = _blank(tool_name)
    act = _blank(action)
    if tool is not None:
        tool = tool.lower()
    if act is not None:
        act = act.lower()
    if tool not in TOOL_NAMES:
        tool = None
    if act not in ACTION_TO_TOOL:
        act = None
    if tool is None and act is not None:
        tool = ACTION_TO_TOOL[act]
    if tool is not None and act is not None and ACTION_TO_TOOL[act] != tool:
        return None, None, "도구와 action이 맞지 않아 호출하지 않았습니다."
    if (tool is None) != (act is None):
        return None, None, "도구 호출 형식이 맞지 않아 호출하지 않았습니다."
    return tool, act, None


def _arguments(value: Any, tool_name: str | None, action: str | None) -> dict[str, str]:
    parsed = _argument_object(value)
    if parsed is None:
        return {}
    method = TOOL_METHODS.get((tool_name, action))
    allowed = set(ARGUMENT_KEYS) if method is None else _parameter_names(method)
    arguments: dict[str, str] = {}
    for key in ARGUMENT_KEYS:
        if key not in allowed or key not in parsed:
            continue
        text = _blank(parsed.get(key))
        if text is not None:
            arguments[key] = text
    return arguments


def _argument_object(value: Any) -> Mapping[str, Any] | None:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], Mapping):
        value = value[0]
    if not isinstance(value, Mapping):
        return None
    return value


def _parameter_names(method: Any) -> set[str]:
    return {
        name
        for name, parameter in inspect.signature(method).parameters.items()
        if name != "self" and parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    }


def _blank(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return None
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, list):
        items = [text for item in value if (text := _blank(item)) is not None]
        if len(items) == 1:
            return items[0]
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or stripped.lower() in {"null", "none", "undefined"}:
        return None
    return stripped


def _json_slice(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start:end + 1]
    return stripped


def _text(message: str | None) -> AgentAction:
    return AgentAction(None, None, {}, message)


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return False
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(exc, "code", None)
    if code in _TRANSIENT_STATUS:
        return True
    name = type(exc).__name__.lower()
    return any(word in name for word in ("timeout", "connection", "unavailable", "ratelimit", "rate_limit"))


def _usage_int(usage: Any, name: str) -> int:
    value = getattr(usage, name, None)
    
    if type(value) is int and value >= 0:
        return value
    
    return 0