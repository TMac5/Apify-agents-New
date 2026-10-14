"""HTTP client for Google Trends with the retry and IP-rotation logic that keeps runs alive.

Google Trends rate-limits by IP and answers with HTTP 429 (or a redirect to a
"sorry" captcha page). The fix is to abandon that IP: open a fresh proxy session,
get a fresh cookie, back off, and try again. Each client owns one proxy session at
a time and rotates it whenever it gets blocked.
"""

from __future__ import annotations

import asyncio
import random
import secrets
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from .trends_api import WARMUP_URL, strip_xssi


@dataclass
class HttpResponse:
    status: int
    text: str
    url: str


class Transport(Protocol):
    async def request(self, method: str, url: str, params: dict[str, str] | None) -> HttpResponse: ...
    async def close(self) -> None: ...


# Builds a transport for a given proxy URL (None = direct connection).
TransportFactory = Callable[[str | None], Awaitable[Transport]]
# Returns a proxy URL for a session id, or None when no proxy is configured.
ProxyUrlProvider = Callable[[str], Awaitable[str | None]]


class TrendsBlockedError(Exception):
    """Every attempt was rate-limited or blocked."""


class TrendsRequestError(Exception):
    """Google rejected the request itself (bad input); retrying will not help."""


BLOCK_STATUSES = {403, 429}
RETRY_STATUSES = {408, 500, 502, 503, 504}


def is_blocked(resp: HttpResponse) -> bool:
    return resp.status in BLOCK_STATUSES or '/sorry/' in resp.url or 'google.com/sorry' in resp.text[:2000]


class TrendsClient:
    def __init__(
        self,
        transport_factory: TransportFactory,
        proxy_url_provider: ProxyUrlProvider,
        *,
        hl: str = 'en-US',
        geo_hint: str = 'US',
        max_attempts: int = 8,
        base_delay: float = 1.5,
        max_delay: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        log: Any = None,
    ) -> None:
        self._transport_factory = transport_factory
        self._proxy_url_provider = proxy_url_provider
        self._hl = hl
        self._geo_hint = (geo_hint or 'US').split('-')[0]
        self._max_attempts = max(1, max_attempts)
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._sleep = sleep
        self._log = log
        self._transport: Transport | None = None
        self.rotations = 0
        self.requests = 0

    async def _open_session(self) -> None:
        await self._close_transport()
        session_id = f'gt{secrets.token_hex(6)}'
        proxy_url = await self._proxy_url_provider(session_id)
        self._transport = await self._transport_factory(proxy_url)
        # Warm-up visit so Google sets its NID cookie (domain .google.com, so Trends sees it too).
        # The google.com homepage is far less rate-limited than the Trends UI page. A failed
        # warm-up is not fatal: many Trends API calls succeed without the cookie, so we only
        # rotate the IP when the actual API call is blocked.
        try:
            warmup = await self._transport.request('GET', WARMUP_URL, {'hl': self._hl.split('-')[0]})
            self.requests += 1
            if is_blocked(warmup) and self._log:
                self._log.debug(f'Warm-up got status {warmup.status}; continuing without cookie')
        except Exception as exc:  # noqa: BLE001
            if self._log:
                self._log.debug(f'Warm-up failed ({exc}); continuing without cookie')

    async def _close_transport(self) -> None:
        if self._transport is not None:
            try:
                await self._transport.close()
            except Exception:  # noqa: BLE001 - closing a dead session must never crash a run
                pass
            self._transport = None

    async def close(self) -> None:
        await self._close_transport()

    def _backoff(self, attempt: int) -> float:
        return min(self._max_delay, self._base_delay * (2 ** (attempt - 1))) * random.uniform(0.6, 1.2)

    async def get_text(
        self,
        url: str,
        params: dict[str, str] | None = None,
        *,
        method: str = 'GET',
        validate: Callable[[str], Any] | None = None,
    ) -> str:
        """Fetch text with retries. `validate` may raise ContentBlocked to force an IP rotation."""
        last_error = 'unknown error'
        for attempt in range(1, self._max_attempts + 1):
            try:
                if self._transport is None:
                    await self._open_session()
                assert self._transport is not None
                resp = await self._transport.request(method, url, params)
                self.requests += 1
                if resp.status == 405 and method == 'POST':
                    method = 'GET'  # endpoint stopped accepting POST; switch for this and later attempts
                    raise _Retryable('status 405, switching to GET')
                if is_blocked(resp):
                    raise _Blocked(f'status {resp.status}')
                if resp.status in RETRY_STATUSES:
                    raise _Retryable(f'status {resp.status}: {resp.text[:120]!r}')
                if resp.status == 400:
                    raise TrendsRequestError(f'Google rejected the request (400): {resp.text[:200]}')
                if resp.status >= 400:
                    raise _Retryable(f'status {resp.status}: {resp.text[:120]!r}')
                if validate is not None:
                    validate(resp.text)
                return resp.text
            except TrendsRequestError:
                raise
            except _Blocked as exc:
                last_error = f'blocked ({exc})'
                self.rotations += 1
                await self._close_transport()  # next attempt gets a new IP and cookie
            except _Retryable as exc:
                last_error = str(exc)
            except Exception as exc:  # noqa: BLE001 - network errors, proxy errors, timeouts
                last_error = f'{type(exc).__name__}: {exc}'
                self.rotations += 1
                await self._close_transport()
            if attempt < self._max_attempts:
                delay = self._backoff(attempt)
                if self._log:
                    self._log.debug(f'Attempt {attempt} failed ({last_error}); retrying in {delay:.1f}s')
                await self._sleep(delay)
        raise TrendsBlockedError(f'Gave up after {self._max_attempts} attempts: {last_error}')

    async def get_json(self, url: str, params: dict[str, str] | None = None, *, method: str = 'GET') -> Any:
        """Fetch and parse JSON. A non-JSON 200 (consent or interstitial page) counts as a block and is retried."""
        holder: dict[str, Any] = {}

        def validate(text: str) -> None:
            try:
                holder['value'] = strip_xssi(text)
            except ValueError as exc:
                raise _Blocked(f'non-JSON response: {text[:120]!r}') from exc

        await self.get_text(url, params, method=method, validate=validate)
        return holder['value']


class _Blocked(Exception):
    pass


# Public name for validators: raising it means "this IP is being refused, rotate and retry".
ContentBlocked = _Blocked


class _Retryable(Exception):
    pass


async def curl_cffi_transport_factory(proxy_url: str | None) -> Transport:
    """Real transport: curl_cffi impersonates Chrome's TLS fingerprint, which Google trusts more."""
    from curl_cffi.requests import AsyncSession  # imported lazily so tests don't need curl_cffi

    session = AsyncSession(
        impersonate='chrome',
        proxy=proxy_url,
        timeout=30,
        headers={
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://trends.google.com/trends/explore',
        },
    )

    class _CurlTransport:
        async def request(self, method: str, url: str, params: dict[str, str] | None) -> HttpResponse:
            resp = await session.request(method, url, params=params, allow_redirects=True)
            return HttpResponse(status=resp.status_code, text=resp.text, url=str(resp.url))

        async def close(self) -> None:
            await session.close()

    return _CurlTransport()
