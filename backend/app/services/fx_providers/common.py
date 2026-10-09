"""Shared bounded historical-provider HTTP transport and request validation."""
import asyncio
from collections.abc import Sequence
from datetime import date, datetime, timezone

import httpx

from app.core.currency import normalize_currency

MAX_DAYS = 31
MAX_RESPONSE_BYTES = 2_000_000


class ProviderError(Exception):
    """Only fixed, credential-free error codes may cross the adapter boundary."""

    def __init__(self, code: str):
        allowed = {"invalid_provider_payload", "provider_payload_too_large",
                   "provider_authentication_failed", "provider_request_rejected",
                   "provider_transport_failure", "provider_rate_limited", "provider_unavailable"}
        super().__init__(code if code in allowed else "provider_failure")


def validate_request(currencies: Sequence[str], dates: Sequence[date]) -> tuple[list[str], list[date]]:
    quotes = [normalize_currency(code) for code in currencies]
    days = list(dates)
    today = datetime.now(timezone.utc).date()
    if (not quotes or len(quotes) != len(set(quotes)) or not days
            or len(days) != len(set(days)) or len(days) > MAX_DAYS
            or any(type(day) is not date or day >= today for day in days)):
        raise ValueError("invalid_ingestion_request")
    if (max(days) - min(days)).days >= MAX_DAYS or min(days) < date(1999, 1, 1):
        raise ValueError("invalid_ingestion_date_range")
    quotes = sorted(set(quotes) - {"USD"})
    if not quotes:
        raise ValueError("no_foreign_currencies_requested")
    return quotes, sorted(days)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("nonfinite_json_number")


async def _read_response(client: httpx.AsyncClient, url: str) -> tuple[int, str, bytes]:
    async with client.stream("GET", url) as response:
        if response.status_code != 200:
            # Error bodies are neither buffered nor inspected.
            return response.status_code, response.headers.get("Retry-After", ""), b""
        # Request identity encoding and fail closed if ignored: automatic
        # decompression can allocate an unbounded chunk before our size check.
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ProviderError("invalid_provider_payload")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(chunk) > MAX_RESPONSE_BYTES - len(content):
                raise ProviderError("provider_payload_too_large")
            content.extend(chunk)
        return response.status_code, "", bytes(content)


async def fetch_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    for attempt in range(3):
        delay = 0.5 * 2 ** attempt
        try:
            # The deadline covers headers, streaming and response cleanup.
            status, retry_after, content = await asyncio.wait_for(_read_response(client, url), timeout=15)
        except (httpx.TransportError, TimeoutError):
            code = "provider_transport_failure"
        else:
            if status == 200:
                return content
            if status in (401, 403):
                raise ProviderError("provider_authentication_failed")
            if status != 429 and not 500 <= status <= 599:
                raise ProviderError("provider_request_rejected")
            code = "provider_rate_limited" if status == 429 else "provider_unavailable"
            if retry_after.isascii() and retry_after.isdigit() and len(retry_after) <= 6:
                delay = min(5, max(delay, int(retry_after)))
        if attempt == 2:
            raise ProviderError(code) from None
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")
