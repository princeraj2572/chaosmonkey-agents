"""A small dependency-free text-protocol tool-using agent."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence, TypeAlias

Message: TypeAlias = dict[str, Any]
ToolFunction: TypeAlias = Callable[..., Any]


@dataclass
class Tool:
    name: str
    function: ToolFunction
    description: str = ""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.function(*args, **kwargs)


@dataclass
class ToolCall:
    name: str
    arguments: Any
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments, "result": self.result, "error": self.error}


@dataclass
class AgentResult:
    answer: str | None = None
    steps: int = 0
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    error: str | None = None
    finished: bool = False

    @property
    def output(self) -> str | None:
        return self.answer

    @property
    def final(self) -> str | None:
        return self.answer

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        return "success" if self.finished else "incomplete"

    @property
    def success(self) -> bool:
        return self.finished and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer, "final": self.final, "output": self.output,
            "steps": self.steps, "messages": self.messages,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "errors": self.errors, "error": self.error, "finished": self.finished,
            "status": self.status,
        }


@dataclass
class _ParsedResponse:
    kind: str
    answer: str = ""
    name: str = ""
    arguments: Any = None
    error: str | None = None


class Agent:
    """Run a model against named Python tools using a text protocol.

    A model may reply with ``CALL tool {json}`` or the more verbose
    ``TOOL: tool`` / ``INPUT: {json}`` form. A response without a protocol
    marker is treated as the final answer.
    """

    protocol = (
        "Respond with either `FINAL: <answer>` or "
        "`CALL <tool> <JSON arguments>`."
    )

    def __init__(
        self,
        model: Any,
        tools: Mapping[str, ToolFunction | Tool] | Sequence[Tool] | None = None,
        *,
        max_steps: int = 8,
        system: str | None = None,
        system_prompt: str | None = None,
    ) -> None:
        if max_steps < 0:
            raise ValueError("max_steps must be non-negative")
        self.model = model
        self.tools = self._normalise_tools(tools)
        self.max_steps = max_steps
        self.system = system if system is not None else system_prompt

    @staticmethod
    def _normalise_tools(
        tools: Mapping[str, ToolFunction | Tool] | Sequence[Tool] | None,
    ) -> dict[str, Tool]:
        if tools is None:
            return {}
        if isinstance(tools, Mapping):
            return {
                str(name): function if isinstance(function, Tool) else Tool(str(name), function)
                for name, function in tools.items()
            }
        return {tool.name: tool for tool in tools}

    def run(
        self,
        task: str,
        *,
        messages: Sequence[Message] | None = None,
        tools: Mapping[str, ToolFunction | Tool] | Sequence[Tool] | None = None,
    ) -> AgentResult:
        active_tools = self._normalise_tools(tools) if tools is not None else self.tools
        history: list[Message] = [dict(message) for message in (messages or [])]
        if self.system and not any(message.get("role") == "system" for message in history):
            history.insert(0, {"role": "system", "content": self._system_text(active_tools)})
        history.append({"role": "user", "content": task})
        result = AgentResult(messages=history)

        while True:
            if result.steps >= self.max_steps:
                result.error = "max_steps"
                result.messages = history
                return result
            response = self._complete(history)
            text = self._response_text(response)
            history.append({"role": "assistant", "content": text})
            parsed = parse_response(text)
            if parsed.kind in {"final", "plain"}:
                result.answer = parsed.answer if parsed.kind == "final" else text
                result.finished = True
                result.messages = history
                return result
            if parsed.kind == "invalid":
                message = parsed.error or "invalid model response"
                result.errors.append(message)
                history.append({"role": "tool", "name": "protocol", "content": f"TOOL ERROR (protocol): {message}"})
                result.steps += 1
                result.messages = history
                continue

            call = ToolCall(parsed.name, parsed.arguments)
            try:
                if parsed.name not in active_tools:
                    raise LookupError(f"unknown tool: {parsed.name}")
                call.result = self._invoke(active_tools[parsed.name], parsed.arguments)
                content = f"TOOL RESULT ({parsed.name}): {self._serialise_tool_value(call.result)}"
            except Exception as exc:
                call.error = str(exc) or exc.__class__.__name__
                result.errors.append(call.error)
                content = f"TOOL ERROR ({parsed.name}): {call.error}"
            result.tool_calls.append(call)
            result.steps += 1
            history.append({"role": "tool", "name": parsed.name, "content": content})
            result.messages = history

    def _system_text(self, tools: Mapping[str, Tool]) -> str:
        lines = [f"- {name}: {tool.description}" for name, tool in tools.items()]
        available = "\n".join(lines) if lines else "(no tools are available)"
        return f"{self.system}\n\nAvailable tools:\n{available}\n\n{self.protocol}"

    def _complete(self, messages: Sequence[Message]) -> Any:
        if hasattr(self.model, "complete"):
            return self.model.complete(messages)
        if callable(self.model):
            return self.model(messages)
        raise TypeError("model must implement complete(messages) or be callable")

    @staticmethod
    def _response_text(response: Any) -> str:
        if isinstance(response, str):
            return response
        if isinstance(response, Mapping):
            content = response.get("content", response.get("text"))
            if content is not None:
                return str(content)
        raise TypeError("model response must be text")

    @staticmethod
    def _serialise_tool_value(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _invoke(tool: Tool, arguments: Any) -> Any:
        if arguments is None:
            return tool()
        if isinstance(arguments, Mapping):
            return tool(**dict(arguments))
        if isinstance(arguments, list):
            return tool(*arguments)
        return tool(arguments)

    __call__ = run


def parse_response(text: str) -> _ParsedResponse:
    """Parse canonical, compact, and legacy tool-call response forms."""

    stripped = text.strip()
    final = re.match(r"^FINAL:\s?(.*)$", stripped, flags=re.IGNORECASE | re.DOTALL)
    if final:
        return _ParsedResponse("final", answer=final.group(1))
    canonical = re.match(
        r"^TOOL:\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\nINPUT:\s*(.*)$",
        stripped, flags=re.IGNORECASE | re.DOTALL,
    )
    if canonical:
        return _parse_tool(canonical.group(1), canonical.group(2))
    compact = re.match(
        r"^(?:CALL|TOOL):?\s*([A-Za-z_][A-Za-z0-9_.-]*)(?:\s+|\n)(.*)$",
        stripped, flags=re.IGNORECASE | re.DOTALL,
    )
    if compact:
        return _parse_tool(compact.group(1), compact.group(2).strip())
    if re.match(r"^(?:CALL|TOOL)\b", stripped, flags=re.IGNORECASE):
        return _ParsedResponse("invalid", error="tool response must contain a tool name and JSON input")
    return _ParsedResponse("plain", answer=text)


def _parse_tool(name: str, raw_arguments: str) -> _ParsedResponse:
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return _ParsedResponse("invalid", error=f"invalid tool input JSON: {exc.msg}")
    return _ParsedResponse("tool", name=name, arguments=arguments)


parse_protocol = parse_response


__all__ = ["Agent", "AgentResult", "Tool", "ToolCall", "parse_response", "parse_protocol"]
