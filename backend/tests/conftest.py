import json

import pytest

from app.config import LLMConfig
from app.llm.client import Attempt, LLMResponse


class FakeLLM:
    """Stands in for LLMClient: returns a scripted answer and counts calls. Only the LLM
    transport is faked; checking, caching, measuring and ranking run for real in the tests."""

    def __init__(self, candidates=None, *, text=None, fail=False, provider="ollama"):
        self.cfg = LLMConfig("http://fake", "fake-model", None, "fake-groq", 0.0, 42, 5)
        self.calls = 0
        self._text = text if text is not None else json.dumps(
            {"candidates": [c if isinstance(c, dict) else {"sql": c, "rationale": "scripted"} for c in candidates or []]})
        self._fail, self._provider = fail, provider

    def chat(self, messages, provider=None, json_mode=False):
        self.calls += 1
        if self._fail:
            return LLMResponse(None, None, None, attempts=[Attempt("ollama", "fake-model", False, 0.0, "ConnectError")],
                               error="ollama: ConnectError")
        return LLMResponse(self._text, self._provider, "fake-model", 0.01,
                           [Attempt(self._provider, "fake-model", True, 0.01)])


@pytest.fixture
def fake_llm():
    return FakeLLM
