"""LLM client: Ollama primary, Groq fallback, and a record of which provider answered.

Every attempt (success or failure) is logged and returned in `attempts`, because
a response from the fallback is a different model and mixing providers across
arms threatens validity (CLAUDE.md). Pass `provider=` to pin one provider with no
fallback, which is what an evaluation run must do.
Temperature and seed come from config (fairness rule 6); both providers get them.
"""
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from app.config import LLMConfig, llm_config

log = logging.getLogger("llm")
PROVIDERS = ("ollama", "groq")


@dataclass
class Attempt:
    provider: str
    model: str
    ok: bool
    latency_s: float
    error: str = ""


@dataclass
class LLMResponse:
    text: str | None
    provider: str | None  # who answered; None if everyone failed
    model: str | None
    latency_s: float = 0.0
    attempts: list[Attempt] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class LLMClient:
    def __init__(self, cfg: LLMConfig | None = None, transport: httpx.BaseTransport | None = None,
                 log_path: Path | None = None):
        self.cfg = cfg or llm_config()
        self._http = httpx.Client(transport=transport, timeout=self.cfg.timeout_s)
        self.log_path = log_path

    def chat(self, messages: list[dict], provider: str | None = None, json_mode: bool = False) -> LLMResponse:
        if provider is not None and provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        order = [provider] if provider else list(PROVIDERS)
        resp = LLMResponse(None, None, None)
        for name in order:
            model = self.cfg.ollama_model if name == "ollama" else self.cfg.groq_model
            t = time.perf_counter()
            try:
                text = self._call(name, messages, json_mode)
                if not text or not text.strip():
                    raise ValueError("empty response")
                att = Attempt(name, model, True, round(time.perf_counter() - t, 3))
                resp.text, resp.provider, resp.model, resp.latency_s = text, name, model, att.latency_s
            except Exception as e:  # any failure: record it and fall through to the next provider
                att = Attempt(name, model, False, round(time.perf_counter() - t, 3), f"{type(e).__name__}: {str(e)[:200]}")
            resp.attempts.append(att)
            self._log(att)
            if att.ok:
                return resp
        resp.error = "; ".join(f"{a.provider}: {a.error}" for a in resp.attempts)
        return resp

    def _call(self, name: str, messages: list[dict], json_mode: bool) -> str:
        c = self.cfg
        if name == "ollama":
            body = {"model": c.ollama_model, "messages": messages, "stream": False,
                    "options": {"temperature": c.temperature, "seed": c.seed}}
            if json_mode:
                body["format"] = "json"
            r = self._http.post(f"{c.ollama_host}/api/chat", json=body)
            r.raise_for_status()
            return r.json()["message"]["content"]
        if not c.groq_api_key:
            raise RuntimeError("GROQ_API_KEY not set")
        body = {"model": c.groq_model, "messages": messages, "temperature": c.temperature, "seed": c.seed}
        if json_mode:
            body["response_format"] = {"type": "json_object"}  # Groq requires the word JSON in the prompt
        r = self._http.post("https://api.groq.com/openai/v1/chat/completions", json=body,
                            headers={"Authorization": f"Bearer {c.groq_api_key}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def _log(self, a: Attempt) -> None:
        log.info("llm provider=%s model=%s ok=%s latency=%.2fs %s", a.provider, a.model, a.ok, a.latency_s, a.error)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(a)) + "\n")
