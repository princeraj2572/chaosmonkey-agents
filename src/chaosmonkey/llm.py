"""Model interfaces and small standard-library HTTP adapters."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, Protocol, Sequence, TypeAlias
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

Message: TypeAlias = dict[str, Any]


class Model(Protocol):
    """The interface consumed by :class:`chaosmonkey.agent.Agent`."""

    def complete(self, messages: Sequence[Message]) -> str:
        """Return the next text response for a chat message history."""


def _content_from_response(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, Mapping):
                message = choice.get("message")
                if isinstance(message, Mapping) and "content" in message:
                    return _content_from_response(message["content"])
                if "text" in choice:
                    return _content_from_response(choice["text"])
        content = payload.get("content")
        if isinstance(content, list):
            parts = [str(block.get("text", "")) for block in content
                     if isinstance(block, Mapping) and block.get("type") == "text"]
            if parts:
                return "".join(parts)
        if isinstance(content, str):
            return content
        for key in ("output", "response", "text"):
            if key in payload:
                return _content_from_response(payload[key])
    if isinstance(payload, list):
        return "".join(_content_from_response(item) for item in payload)
    raise ValueError("model response did not contain text content")


class _HTTPModel:
    endpoint: str

    def __init__(self, api_key: str | None = None, model: str = "", *,
                 base_url: str | None = None, timeout: float = 60.0,
                 headers: Mapping[str, str] | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = (base_url or self.endpoint).rstrip("/")
        self.headers = dict(headers or {})

    def complete(self, messages: Sequence[Message]) -> str:
        payload, headers, url = self._request_parts(messages)
        request = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"model request failed ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"model request failed: {exc.reason}") from exc
        try:
            body = json.loads(raw.decode("utf-8"))
            return _content_from_response(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("model returned invalid JSON") from exc
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

    def __call__(self, messages: Sequence[Message]) -> str:
        return self.complete(messages)

    def _request_parts(self, messages: Sequence[Message]):
        raise NotImplementedError


class AnthropicClient(_HTTPModel):
    """Minimal Anthropic Messages API client using :mod:`urllib` only."""

    endpoint = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str | None = None,
                 model: str = "claude-3-5-sonnet-latest", *,
                 base_url: str | None = None, timeout: float = 60.0,
                 max_tokens: int = 1024, system: str | None = None,
                 headers: Mapping[str, str] | None = None) -> None:
        super().__init__(api_key or os.environ.get("ANTHROPIC_API_KEY"), model,
                         base_url=base_url, timeout=timeout, headers=headers)
        self.max_tokens = max_tokens
        self.system = system

    def _request_parts(self, messages: Sequence[Message]):
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if role == "system":
                system_parts.append(str(content))
                continue
            if role == "tool":
                role = "user"
                content = f"Tool result ({message.get('name', 'tool')}):\n{content}"
            converted.append({"role": "assistant" if role == "assistant" else "user",
                              "content": content})
        payload: dict[str, Any] = {"model": self.model, "max_tokens": self.max_tokens,
                                   "messages": converted}
        combined = "\n\n".join(system_parts)
        if self.system:
            combined = f"{self.system}\n\n{combined}" if combined else self.system
        if combined:
            payload["system"] = combined
        headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        headers.update(self.headers)
        url = self.base_url if self.base_url.endswith("/messages") else f"{self.base_url}/v1/messages"
        return payload, headers, url


class OpenAIClient(_HTTPModel):
    """Minimal OpenAI-compatible chat-completions client using ``urllib``."""

    endpoint = "https://api.openai.com/v1/chat/completions"

    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini", *,
                 base_url: str | None = None, timeout: float = 60.0,
                 temperature: float | None = None,
                 headers: Mapping[str, str] | None = None) -> None:
        super().__init__(api_key or os.environ.get("OPENAI_API_KEY"), model,
                         base_url=base_url, timeout=timeout, headers=headers)
        self.temperature = temperature

    def _request_parts(self, messages: Sequence[Message]):
        payload: dict[str, Any] = {"model": self.model,
                                   "messages": [dict(message) for message in messages]}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        headers.update(self.headers)
        url = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/v1/chat/completions"
        return payload, headers, url


AnthropicModel = AnthropicClient
OpenAIModel = OpenAIClient
Anthropic = AnthropicClient
OpenAI = OpenAIClient


class MockModel:
    """Deterministic model for examples and offline tests."""

    def __init__(self, style: str = "naive", messages: Any = None) -> None:
        self.style = style.lower()
        self._responses = messages
        self.messages: list[Message] = []
        self._index = 0

    def complete(self, messages: Sequence[Message]) -> str:
        self.messages = [dict(message) for message in messages]
        if callable(self._responses):
            return str(self._responses(messages))
        if isinstance(self._responses, (list, tuple)):
            if self._index >= len(self._responses):
                return "FINAL:"
            response = self._responses[self._index]
            self._index += 1
            return str(response)
        if self.style in {"naive", "mock-naive"}:
            return "FINAL: " + _last_user_text(messages)
        result = next((m.get("content", "") for m in reversed(messages)
                       if m.get("role") == "tool"), None)
        if result is not None:
            return f"FINAL: {result}"
        return "FINAL: " + _last_user_text(messages)

    __call__ = complete


def _last_user_text(messages: Sequence[Message]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def mock_brain(style: str = "naive", messages: Any = None) -> MockModel:
    """Return a configurable ``mock-naive`` or ``mock-careful`` model.

    ``messages`` may be a response sequence or a callback accepting the
    current history, so examples can supply an exact deterministic transcript.
    """
    return MockModel(style, messages)


LLM = Model
MockBrain = MockModel
