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


# ── 429 카운터 ─────────────────────────────────────────────────────────────
# 백오프가 429 를 조용히 삼켜, 수집이 한도에 얼마나 근접했는지 알 수가 없었다.
# 동시성을 안전하게 올리려면 "지금 몇 번 맞고 있나"를 봐야 한다.


def test_counts_429_across_retries():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429 if calls["n"] < 3 else 200, json=["ok"])

    with _client(handler) as c:
        c.get("/fconline/v1/match")
    assert c.rate_limited_count == 2       # 429 두 번을 셌다


def test_counts_5xx_separately_from_429():
    seq = [503, 429, 200]
    calls = {"n": 0}

    def handler(request):
        r = seq[calls["n"]]
        calls["n"] += 1
        return httpx.Response(r, json=["ok"])

    with _client(handler) as c:
        c.get("/fconline/v1/match")
    assert c.rate_limited_count == 1       # 429 만
    assert c.server_error_count == 1       # 5xx 는 따로


def test_counters_start_at_zero_and_stay_zero_on_success():
    with _client(lambda req: httpx.Response(200, json=["ok"])) as c:
        c.get("/fconline/v1/match")
    assert c.rate_limited_count == 0 and c.server_error_count == 0


def test_async_counts_429():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429 if calls["n"] < 3 else 200, json={"ok": 1})

    async def go():
        s = Settings(max_requests_per_sec=1000, backoff_base_sec=0.0, backoff_max_sec=0.0)
        async with AsyncResilientClient(s, transport=httpx.MockTransport(handler),
                                        concurrency=5) as c:
            await c.get("/a")
            return c.rate_limited_count

    assert asyncio.run(go()) == 2


def test_429_halves_the_live_rate():
    """429 를 맞으면 클라이언트의 실제 레이트가 반토막나야 한다(카운터만이 아니라)."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429 if calls["n"] < 2 else 200, json=["ok"])

    s = Settings(max_requests_per_sec=15, backoff_base_sec=0.0, backoff_max_sec=0.0)
    c = ResilientClient(s, transport=httpx.MockTransport(handler), sleep=lambda _x: None)
    before = c.rate.current
    with c:
        c.get("/fconline/v1/match")
    assert before == 15
    assert c.rate.current == 7.5      # 429 한 번 → 반토막
