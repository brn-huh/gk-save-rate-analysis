"""복원력 있는 HTTP 클라이언트 (T3).

넥슨 Open API는 429(Too Many Requests)를 낸다. 긴 스노우볼 크롤이
한 번의 429나 일시 5xx로 통째로 죽지 않도록:
  - 토큰버킷 레이트리밋으로 요청 간격을 강제하고,
  - 429/5xx에 지수 백오프 + Retry-After 헤더 존중으로 재시도한다.

레이트리밋 수치(max_requests_per_sec)는 T0 스파이크에서 429를 실측한 뒤
config.Settings에서 조정한다.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Callable

import httpx

from .config import DEFAULT, BASE_URL, Settings, api_key


class RateLimiter:
    """단순 토큰버킷: 초당 rate 개까지 허용, 부족하면 sleep."""

    def __init__(self, rate_per_sec: float) -> None:
        self.min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._last = 0.0

    def acquire(self, *, sleep: Callable[[float], None] = time.sleep) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        wait = self._last + self.min_interval - now
        if wait > 0:
            sleep(wait)
            now = time.monotonic()
        self._last = now


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class ResilientClient:
    """넥슨 Open API용 얇은 클라이언트. get()은 파싱된 JSON을 돌려준다.

    time 함수는 테스트에서 주입할 수 있게 파라미터로 빼뒀다.
    """

    def __init__(
        self,
        settings: Settings = DEFAULT,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.s = settings
        self._sleep = sleep
        self._limiter = RateLimiter(settings.max_requests_per_sec)
        # 백오프가 429/5xx 를 삼키므로, 한도에 얼마나 근접했는지 보려면 직접 센다.
        self.rate_limited_count = 0   # 429
        self.server_error_count = 0   # 5xx
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=settings.request_timeout_sec,
            headers={"x-nxopen-api-key": api_key()},
            transport=transport,
        )

    def __enter__(self) -> "ResilientClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _backoff_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.s.backoff_max_sec)
            except ValueError:
                pass
        # 지수 백오프 + 지터
        base = self.s.backoff_base_sec * (2 ** attempt)
        return min(base, self.s.backoff_max_sec) * (0.5 + random.random() / 2)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        attempt = 0
        while True:
            self._limiter.acquire(sleep=self._sleep)
            try:
                resp = self._client.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt >= self.s.max_retries:
                    raise ApiError(-1, f"transport error: {exc}") from exc
                self._sleep(self._backoff_delay(attempt, None))
                attempt += 1
                continue

            if resp.status_code == 200:
                return resp.json()

            # 429 / 5xx → 재시도. 4xx(400/403/404 등)는 즉시 실패.
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if resp.status_code == 429:
                    self.rate_limited_count += 1
                else:
                    self.server_error_count += 1
                if attempt >= self.s.max_retries:
                    raise ApiError(resp.status_code, resp.text)
                self._sleep(self._backoff_delay(attempt, resp.headers.get("Retry-After")))
                attempt += 1
                continue

            raise ApiError(resp.status_code, resp.text)


class AsyncRateLimiter:
    """비동기 토큰버킷: 여러 코루틴이 공유해 전체 요청률을 rate 이하로 유지.

    스케줄링만 짧게 직렬화하고 요청 자체는 동시에 in-flight → 네트워크 지연을
    겹쳐 레이트 예산을 꽉 채운다(순차 대기 병목 제거).
    """

    def __init__(self, rate_per_sec: float) -> None:
        self.min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._next - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = asyncio.get_event_loop().time()
            self._next = max(self._next, now) + self.min_interval


class AsyncResilientClient:
    """동시 요청 버전. get()은 파싱된 JSON을 돌려준다.

    concurrency 개까지 동시에 in-flight, 전체 요청률은 rate 로 제한. 429/5xx는
    지수 백오프 재시도. 레이트리밋이 한도를 지키므로 동시성을 올려도 안전.
    """

    def __init__(
        self,
        settings: Settings = DEFAULT,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = 10,
    ) -> None:
        self.s = settings
        self._limiter = AsyncRateLimiter(settings.max_requests_per_sec)
        self._sem = asyncio.Semaphore(concurrency)
        # 백오프가 429/5xx 를 삼키므로, 한도에 얼마나 근접했는지 보려면 직접 센다.
        self.rate_limited_count = 0   # 429
        self.server_error_count = 0   # 5xx
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=settings.request_timeout_sec,
            headers={"x-nxopen-api-key": api_key()},
            transport=transport,
        )

    async def __aenter__(self) -> "AsyncResilientClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _backoff_delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.s.backoff_max_sec)
            except ValueError:
                pass
        base = self.s.backoff_base_sec * (2 ** attempt)
        return min(base, self.s.backoff_max_sec) * (0.5 + random.random() / 2)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        attempt = 0
        while True:
            await self._limiter.acquire()
            try:
                async with self._sem:
                    resp = await self._client.get(path, params=params)
            except httpx.TransportError as exc:
                if attempt >= self.s.max_retries:
                    raise ApiError(-1, f"transport error: {exc}") from exc
                await asyncio.sleep(self._backoff_delay(attempt, None))
                attempt += 1
                continue

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if resp.status_code == 429:
                    self.rate_limited_count += 1
                else:
                    self.server_error_count += 1
                if attempt >= self.s.max_retries:
                    raise ApiError(resp.status_code, resp.text)
                await asyncio.sleep(self._backoff_delay(attempt, resp.headers.get("Retry-After")))
                attempt += 1
                continue
            raise ApiError(resp.status_code, resp.text)
