"""복원력 HTTP 테스트: 429 → 백오프 → 성공, 4xx 즉시 실패."""

import asyncio

import httpx
import pytest

from gksave.config import Settings
from gksave.http import ApiError, AsyncResilientClient, ResilientClient


def _client(handler, **kw):
    s = Settings(max_requests_per_sec=1000, backoff_base_sec=0.0, backoff_max_sec=0.0, **kw)
    # sleep 은 실제로 안 재우도록 no-op 주입
    return ResilientClient(s, transport=httpx.MockTransport(handler), sleep=lambda _x: None)


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": "rate"})
        return httpx.Response(200, json=["M1", "M2"])

    with _client(handler) as c:
        assert c.get("/fconline/v1/match", {"matchtype": 50}) == ["M1", "M2"]
    assert calls["n"] == 3       # 429 두 번 재시도 후 성공


def test_gives_up_after_max_retries():
    def handler(request):
        return httpx.Response(429)

    with _client(handler, max_retries=2) as c:
        with pytest.raises(ApiError) as e:
            c.get("/fconline/v1/match")
    assert e.value.status == 429


def test_4xx_fails_immediately():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    with _client(handler) as c:
        with pytest.raises(ApiError) as e:
            c.get("/fconline/v1/match")
    assert e.value.status == 400
    assert calls["n"] == 1       # 재시도 없이 즉시 실패


def test_async_retries_and_concurrency():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": calls["n"]})

    async def go():
        s = Settings(max_requests_per_sec=1000, backoff_base_sec=0.0, backoff_max_sec=0.0)
        async with AsyncResilientClient(s, transport=httpx.MockTransport(handler),
                                        concurrency=5) as c:
            # 동시 요청 3개 — 다 성공해야
            return await asyncio.gather(c.get("/a"), c.get("/b"), c.get("/c"))

    res = asyncio.run(go())
    assert len(res) == 3 and all("ok" in r for r in res)   # 429 재시도 후 전부 성공
