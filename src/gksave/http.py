"""복원력 있는 HTTP 클라이언트 (T3).

넥슨 Open API는 429(Too Many Requests)를 낸다. 긴 스노우볼 크롤이
한 번의 429나 일시 5xx로 통째로 죽지 않도록:
  - 토큰버킷 레이트리밋으로 요청 간격을 강제하고,
  - 429/5xx에 지수 백오프 + Retry-After 헤더 존중으로 재시도한다.

레이트리밋 수치(max_requests_per_sec)는 T0 스파이크에서 429를 실측한 뒤
config.Settings에서 조정한다.
"""

from __future__ import annotations

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
                if attempt >= self.s.max_retries:
                    raise ApiError(resp.status_code, resp.text)
                self._sleep(self._backoff_delay(attempt, resp.headers.get("Retry-After")))
                attempt += 1
                continue

            raise ApiError(resp.status_code, resp.text)
