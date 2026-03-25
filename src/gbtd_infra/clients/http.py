from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse
import asyncio
import hashlib
import time
from typing import Any, Optional

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from gbtd_infra.config import AppConfig
from gbtd_infra.models import RateLimitEvent


@dataclass
class RequestAttempt:
    status_code: int
    retry_after: Optional[float]
    response_headers: dict[str, str]


class HostTokenBucket:
    def __init__(self, rps: float, capacity: int) -> None:
        self.rate = rps
        self.capacity = max(1, capacity)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()

    def consume(self, amount: float = 1.0) -> float:
        now = time.monotonic()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        if self.tokens >= amount:
            self.tokens -= amount
            return 0.0
        deficit = amount - self.tokens
        wait = deficit / self.rate
        self.tokens = 0.0
        return wait


class PoliteHttpClient:
    """Host-scoped rate limiter + retry policy + raw payload helpers."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._buckets: dict[str, HostTokenBucket] = defaultdict(
            lambda: HostTokenBucket(config.rate_limits.per_host_rps, config.rate_limits.burst)
        )
        self._inflight_concurrency: dict[str, int] = defaultdict(int)
        self._locks = defaultdict(asyncio.Lock)
        self._session = httpx.AsyncClient(timeout=config.timeout_seconds)

    async def _host(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def _acquire(self, host: str) -> float:
        # concurrency cap — spin outside lock to avoid blocking other hosts
        cap = self.config.rate_limits.host_concurrency
        while True:
            async with self._locks[host]:
                if self._inflight_concurrency[host] < cap:
                    self._inflight_concurrency[host] += 1
                    break
            await asyncio.sleep(0.05)

        wait = self._buckets[host].consume()
        if wait > 0:
            await asyncio.sleep(wait)
        return wait

    async def _release(self, host: str) -> None:
        async with self._locks[host]:
            self._inflight_concurrency[host] = max(0, self._inflight_concurrency[host] - 1)

    @staticmethod
    def _hash_request(method: str, params: dict[str, Any] | None, headers: dict[str, str] | None) -> str:
        h = hashlib.sha256()
        h.update(method.upper().encode())
        h.update(repr(params).encode())
        if headers:
            h.update(repr(sorted(headers.items())).encode())
        return h.hexdigest()

    @staticmethod
    def _is_retryable(status: int) -> bool:
        return status in (408, 409, 423, 429, 500, 502, 503, 504)

    @staticmethod
    def parse_retry_after(headers: dict[str, str]) -> Optional[float]:
        retry_after = headers.get("retry-after")
        if not retry_after:
            return None
        try:
            return float(retry_after)
        except ValueError:
            try:
                dt = datetime.strptime(retry_after, "%a, %d %b %Y %H:%M:%S GMT")
                return max(0.0, (dt - datetime.utcnow()).total_seconds())
            except Exception:
                return None

    async def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        host = await self._host(url)
        await self._acquire(host)
        try:
            # jitter helps de-synchronization, not evasion
            request_headers = {"User-Agent": self.config.user_agent}
            if headers:
                request_headers.update(headers)

            attempt = 0
            async for retry_state in AsyncRetrying(
                stop=stop_after_attempt(5),
                wait=wait_exponential_jitter(
                    initial=self.config.rate_limits.backoff_base_seconds,
                    max=self.config.rate_limits.backoff_max_seconds,
                    jitter=self.config.rate_limits.retry_jitter,
                ),
                retry=retry_if_exception_type((httpx.HTTPStatusError,)),
                reraise=True,
            ):
                with retry_state:
                    attempt += 1
                    response = await self._session.request(
                        method=method,
                        url=url,
                        headers=request_headers,
                        params=params,
                        json=json,
                    )

                    if response.status_code in {401, 403, 404}:
                        return response

                    if self._is_retryable(response.status_code):
                        raise httpx.HTTPStatusError("retryable", request=response.request, response=response)

                    response.raise_for_status()
                    return response
            raise RuntimeError("unreachable")
        finally:
            await self._release(host)

    async def get(self, url: str, headers: dict[str, str] | None = None, params=None) -> httpx.Response:
        return await self.request("GET", url, headers=headers, params=params)

    async def post(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return await self.request("POST", url, headers=headers, params=params, json=json)

    async def close(self) -> None:
        await self._session.aclose()


class HostEventLogger:
    @staticmethod
    def parse(response: httpx.Response, method: str) -> RequestAttempt:
        headers = {k.lower(): v for k, v in response.headers.items()}
        return RequestAttempt(
            status_code=response.status_code,
            retry_after=PoliteHttpClient.parse_retry_after(headers),
            response_headers=dict(headers),
        )

    @staticmethod
    def to_rate_limit_event(
        payload: RateLimitEvent,
        family_id: int,
        host: str,
        path: str,
        attempt: RequestAttempt,
    ) -> RateLimitEvent:
        return RateLimitEvent(
            family_id=family_id,
            host=host,
            path=path,
            status_code=attempt.status_code,
            retry_after_seconds=attempt.retry_after,
            decision="record",
        )
