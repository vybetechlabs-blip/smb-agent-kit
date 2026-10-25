"""LLM providers behind one interface: scripted / ollama / anthropic.

The scripted provider makes tests deterministic. Ollama is used only if it is already running
locally with the model pulled (never pulled from here). Anthropic needs ANTHROPIC_API_KEY in the
environment and an explicit model id (no default model is assumed).

Every call returns a CompletionResult with the text plus tokens in, tokens out, latency and
retries, so cost and speed can be measured per call. `structured_output` turns one call into
parsed data or a failure reason that a project can route to a person.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError


@dataclass
class CompletionResult:
    text: str
    tokens_in: int
    tokens_out: int
    latency_s: float
    model: str
    retries: int = 0


class LLMProvider(Protocol):
    name: str
    model: str
    settings: dict

    def complete(self, system: str, prompt: str, schema: dict | None = None) -> CompletionResult:
        """One call. With `schema`, the reply must be JSON that matches it."""
        ...


class ProviderUnavailable(RuntimeError):
    pass


class TransientProviderError(ProviderUnavailable):
    """A 5xx answer or a dropped connection: the only failures that are retried."""


class ScriptedLLM:
    """Returns canned responses in order. For tests and offline demos."""

    name = "scripted"
    model = "scripted"

    def __init__(self, responses: Iterable[str]):
        self.settings: dict = {}
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str, schema: dict | None = None) -> CompletionResult:
        self.calls.append((system, prompt))
        if not self._responses:
            raise ProviderUnavailable("ScriptedLLM has no responses left")
        return CompletionResult(self._responses.pop(0), 0, 0, 0.0, self.model)


class OllamaLLM:
    """Local Ollama over HTTP. Uses 127.0.0.1: `localhost` adds IPv6 fallback delay on Windows.

    Sampling is pinned (temperature 0, fixed seed) so repeated passes are comparable.
    """

    name = "ollama"

    def __init__(
        self,
        model: str,
        host: str = "http://127.0.0.1:11434",
        timeout: float = 600,
        temperature: float = 0.0,
        seed: int = 42,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.settings = {"temperature": temperature, "seed": seed}
        self.retries = 0

    def _request(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{self.host}{path}", data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                raise TransientProviderError(f"Ollama answered {exc.code}") from exc
            raise ProviderUnavailable(f"Ollama answered {exc.code}") from exc
        except (urllib.error.URLError, ConnectionError) as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise ProviderUnavailable(f"Ollama timed out at {self.host}") from exc
            raise TransientProviderError(f"Ollama not reachable at {self.host}: {exc}") from exc
        except OSError as exc:  # timeouts and anything else: not retried
            raise ProviderUnavailable(f"Ollama call failed at {self.host}: {exc}") from exc

    def warm_up(self) -> None:
        """Load the model with one tiny call before scoring. Never scored; a failure is noted only.

        Local model servers can crash while loading a model (HTTP 500), which made the first real
        request after a model load fail.
        """
        start = time.perf_counter()
        try:
            self.complete("Reply with the word ok.", "ok")
            self.settings["warm_up"] = f"ok in {time.perf_counter() - start:.1f}s"
        except ProviderUnavailable as exc:
            self.settings["warm_up"] = f"failed ({exc})"
        self.retries = 0  # retries during warm-up are not counted against scored calls

    def check_pulled(self) -> None:
        """Refuse to run (and never pull) unless the model is already on this machine."""
        names = {m["name"] for m in self._request("/api/tags").get("models", [])}
        if self.model not in names:
            raise ProviderUnavailable(f"Ollama model {self.model!r} is not pulled; not pulling it")

    def complete(self, system: str, prompt: str, schema: dict | None = None) -> CompletionResult:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": dict(self.settings),
        }
        if schema is not None:
            payload["format"] = schema
        payload["options"].pop("warm_up", None)
        retried = 0
        start = time.perf_counter()
        try:
            body = self._request("/api/chat", payload)
        except TransientProviderError:
            # One retry, on a 5xx or a dropped connection only. A reply that fails parsing or the
            # schema is never retried: that check happens after this returns.
            retried = 1
            self.retries += 1
            start = time.perf_counter()
            body = self._request("/api/chat", payload)
        latency = time.perf_counter() - start
        return CompletionResult(
            text=body["message"]["content"],
            tokens_in=int(body.get("prompt_eval_count", 0)),
            tokens_out=int(body.get("eval_count", 0)),
            latency_s=latency,
            model=self.model,
            retries=retried,
        )


class AnthropicLLM:
    """Anthropic Messages API. Reads ANTHROPIC_API_KEY only; needs an explicit model id."""

    name = "anthropic"

    def __init__(self, model: str, max_tokens: int = 4096, effort: str = "low"):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ProviderUnavailable("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderUnavailable("install the extra: uv sync --extra llm") from exc
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        # The Messages API takes no temperature or seed on this model; effort is the only knob.
        self.settings = {"effort": effort, "temperature": "not accepted", "seed": "not offered"}
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system: str, prompt: str, schema: dict | None = None) -> CompletionResult:
        output_config: dict = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        start = time.perf_counter()
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
        )
        latency = time.perf_counter() - start
        if msg.stop_reason == "refusal":
            raise ProviderUnavailable(f"{self.model} declined the request")
        text = "".join(block.text for block in msg.content if block.type == "text")
        return CompletionResult(
            text=text,
            tokens_in=msg.usage.input_tokens,
            tokens_out=msg.usage.output_tokens,
            latency_s=latency,
            model=self.model,
        )


class CachedLLM:
    """Wraps a provider and answers a repeated (system, prompt, schema) from memory.

    Used to score the same model outputs twice (guard off and guard on) without a second call.
    """

    def __init__(self, inner: LLMProvider):
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.settings = getattr(inner, "settings", {})
        self._memo: dict[str, CompletionResult] = {}

    def complete(self, system: str, prompt: str, schema: dict | None = None) -> CompletionResult:
        key = json.dumps([system, prompt, schema], sort_keys=True)
        if key not in self._memo:
            self._memo[key] = self.inner.complete(system, prompt, schema)
        return self._memo[key]


def make_provider(name: str, *, model: str | None = None, **kwargs) -> LLMProvider:
    if name == "scripted":
        return ScriptedLLM(kwargs.get("responses", []))
    if not model:
        raise ProviderUnavailable(f"provider {name!r} needs an explicit model id")
    if name == "ollama":
        provider = OllamaLLM(model, **kwargs)
        provider.check_pulled()
        provider.warm_up()
        return provider
    if name == "anthropic":
        return AnthropicLLM(model, **kwargs)
    raise ValueError(f"unknown provider: {name}")


M = TypeVar("M", bound=BaseModel)


def parse_json_reply(text: str, model: type[M]) -> M:
    """Parse a model's JSON reply into `model`. Tolerates a fenced block; anything else raises."""
    stripped = text.strip()
    fenced = re.search(r"\{.*\}", stripped, re.S)
    payload = fenced.group(0) if fenced else stripped
    return model.model_validate(json.loads(payload))


def structured_output(
    provider: LLMProvider, system: str, prompt: str, schema: dict, model: type[M]
) -> tuple[M | None, CompletionResult | None, str | None]:
    """One structured call. Returns (parsed, call record, failure).

    `failure` is None on success, the exception type name when the call itself failed (provider
    down, refusal, timeout), or "invalid_reply" when the reply did not match the schema. Nothing
    is retried here; providers retry transient errors themselves.
    """
    try:
        result = provider.complete(system, prompt, schema)
    except Exception as exc:
        return None, None, type(exc).__name__
    try:
        return parse_json_reply(result.text, model), result, None
    except (ValueError, ValidationError):
        return None, result, "invalid_reply"
