import json

import httpx

from app.config import LLMConfig
from app.llm.client import LLMClient

CFG = LLMConfig("http://ollama.test", "qwen-test", "gsk_test", "groq-test", temperature=0.0, seed=42, timeout_s=5)
MSG = [{"role": "user", "content": "hi"}]


def client(handler, cfg=CFG, **kw):
    return LLMClient(cfg, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)  # never really sleep


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
    o = next(b for u, b in seen if "ollama.test" in u)  # (a 500 is retried, so there are several ollama requests)
    g = next(b for u, b in seen if "groq.com" in u)
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


def test_rate_limit_is_retried_with_retry_after_then_succeeds():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, json={"message": {"content": "finally"}})

    r = LLMClient(CFG, transport=httpx.MockTransport(handler), sleep=sleeps.append).chat(MSG, provider="ollama")
    assert r.text == "finally" and len(calls) == 3 and sleeps == [7.0, 7.0]
    assert len(r.attempts) == 1  # one logical call, however many HTTP retries it took


def test_persistent_rate_limit_ends_as_a_logged_failure():
    sleeps = []
    r = LLMClient(CFG, transport=httpx.MockTransport(lambda req: httpx.Response(429)), sleep=sleeps.append).chat(MSG, provider="ollama")
    assert r.text is None and "429" in r.error and len(sleeps) == 3


def test_client_errors_other_than_429_are_not_retried():
    sleeps, calls = [], []
    LLMClient(CFG, transport=httpx.MockTransport(lambda req: (calls.append(1), httpx.Response(404))[1]),
              sleep=sleeps.append).chat(MSG, provider="ollama")
    assert len(calls) == 1 and sleeps == []


def test_dropped_connection_is_ridden_out_with_long_backoff():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("getaddrinfo failed")
        return httpx.Response(200, json={"message": {"content": "back online"}})

    r = LLMClient(CFG, transport=httpx.MockTransport(handler), sleep=sleeps.append).chat(MSG, provider="ollama")
    assert r.text == "back online" and sleeps == [5.0, 15.0]


def test_a_persistent_outage_is_a_logged_failure_after_the_last_try():
    sleeps = []

    def handler(request):
        raise httpx.ConnectError("getaddrinfo failed")

    r = LLMClient(CFG, transport=httpx.MockTransport(handler), sleep=sleeps.append).chat(MSG, provider="ollama")
    assert r.text is None and "ConnectError" in r.error and sleeps == [5.0, 15.0, 45.0]


def test_a_long_retry_after_is_honoured_not_cut_to_60_seconds():
    calls, sleeps = [], []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "200"})
        return httpx.Response(200, json={"message": {"content": "ok after the full wait"}})

    r = LLMClient(CFG, transport=httpx.MockTransport(handler), sleep=sleeps.append).chat(MSG, provider="ollama")
    assert r.text == "ok after the full wait" and sleeps == [200.0]


def test_an_absurd_retry_after_is_capped_at_fifteen_minutes():
    sleeps = []
    LLMClient(CFG, transport=httpx.MockTransport(lambda req: httpx.Response(429, headers={"retry-after": "86400"})),
              sleep=sleeps.append).chat(MSG, provider="ollama")
    assert sleeps == [900.0, 900.0, 900.0]
