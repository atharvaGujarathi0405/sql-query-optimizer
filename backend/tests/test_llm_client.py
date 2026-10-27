import json

import httpx

from app.config import LLMConfig
from app.llm.client import LLMClient

CFG = LLMConfig("http://ollama.test", "qwen-test", "gsk_test", "groq-test", temperature=0.0, seed=42, timeout_s=5)
MSG = [{"role": "user", "content": "hi"}]


def client(handler, cfg=CFG, **kw):
    return LLMClient(cfg, transport=httpx.MockTransport(handler), **kw)


def ollama_ok(request):
    if "ollama.test" in str(request.url):
        return httpx.Response(200, json={"message": {"content": "from-ollama"}})
    return httpx.Response(200, json={"choices": [{"message": {"content": "from-groq"}}]})


def test_ollama_answers_first_and_provider_is_recorded():
    r = client(ollama_ok).chat(MSG)
    assert (r.text, r.provider, r.model) == ("from-ollama", "ollama", "qwen-test")
    assert [a.provider for a in r.attempts] == ["ollama"]


def test_temperature_and_seed_are_sent_to_both_providers():
    seen = []

    def handler(request):
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(500) if "ollama.test" in str(request.url) else ollama_ok(request)

    r = client(handler).chat(MSG, json_mode=True)
    assert r.provider == "groq"
    o, g = seen[0][1], seen[1][1]
    assert o["options"] == {"temperature": 0.0, "seed": 42} and o["format"] == "json"
    assert g["temperature"] == 0.0 and g["seed"] == 42 and g["response_format"] == {"type": "json_object"}


def test_falls_back_to_groq_when_ollama_is_down_and_logs_both_attempts():
    def handler(request):
        if "ollama.test" in str(request.url):
            raise httpx.ConnectError("refused")
        return ollama_ok(request)

    r = client(handler).chat(MSG)
    assert r.provider == "groq" and r.text == "from-groq"
    assert [(a.provider, a.ok) for a in r.attempts] == [("ollama", False), ("groq", True)]
    assert "ConnectError" in r.attempts[0].error


def test_pinned_provider_never_falls_back():
    def handler(request):
        return httpx.Response(503) if "ollama.test" in str(request.url) else ollama_ok(request)

    r = client(handler).chat(MSG, provider="ollama")
    assert r.text is None and r.provider is None
    assert [a.provider for a in r.attempts] == ["ollama"] and "503" in r.error


def test_everyone_fails_without_raising():
    r = client(lambda req: httpx.Response(500)).chat(MSG)
    assert r.text is None and len(r.attempts) == 2 and not any(a.ok for a in r.attempts)


def test_missing_groq_key_disables_the_fallback_cleanly():
    cfg = LLMConfig("http://ollama.test", "m", None, "g", 0.0, 42, 5)
    r = client(lambda req: httpx.Response(500), cfg).chat(MSG)
    assert r.text is None and "GROQ_API_KEY not set" in r.error


def test_empty_answer_counts_as_failure():
    def handler(request):
        if "ollama.test" in str(request.url):
            return httpx.Response(200, json={"message": {"content": "  "}})
        return ollama_ok(request)

    assert client(handler).chat(MSG).provider == "groq"


def test_every_attempt_is_appended_to_the_jsonl_log(tmp_path):
    log = tmp_path / "calls.jsonl"
    client(lambda req: httpx.Response(500) if "ollama.test" in str(req.url) else ollama_ok(req), log_path=log).chat(MSG)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(r["provider"], r["ok"]) for r in rows] == [("ollama", False), ("groq", True)]
