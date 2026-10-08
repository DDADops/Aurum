"""Completed UTC daily observations, USD base; no derived or filled rates.

https://docs.openexchangerates.org/reference/historical-json
https://docs.openexchangerates.org/reference/authentication
"""
import asyncio
import json
import re
from collections.abc import Sequence
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx

from app.core.currency import normalize_currency
from app.schemas.fx import FxObservation

SOURCE = "openexchangerates:historical:USD"
MAX_DAYS = 31


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


def _parse(content: bytes, day: date, quotes: list[str]) -> list[FxObservation]:
    try:
        data = json.loads(content, parse_float=Decimal, object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
        if not isinstance(data, dict) or data.get("error") or data.get("base") != "USD":
            raise ValueError("invalid_base_or_error")
        stamp = data.get("timestamp")
        if type(stamp) is not int or datetime.fromtimestamp(stamp, timezone.utc).date() != day:
            raise ValueError("wrong_observation_date")
        rates = data["rates"]
        if not isinstance(rates, dict):
            raise ValueError("invalid_rates")
        fetched = datetime.now(timezone.utc)
        result = []
        # Free plans return the full provider catalog. Persist only requested,
        # application-supported quotes, never unsupported/alternative symbols.
        for code in quotes:
            if code not in rates:
                continue  # Orchestrator rejects the incomplete batch before any write.
            rate = rates[code]
            if type(rate) is int:
                rate = Decimal(rate)
            result.append(FxObservation(base_currency="USD", quote_currency=code,
                rate=rate, as_of_date=day, source=SOURCE, fetched_at=fetched))
        if "USD" in rates and (type(rates["USD"]) not in (int, Decimal) or rates["USD"] != 1):
            raise ValueError("invalid_identity_rate")
        return result
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        raise ProviderError("invalid_provider_payload") from None


class OpenExchangeRatesProvider:
    def __init__(self, app_id: str, *, transport: httpx.AsyncBaseTransport | None = None):
        if not re.fullmatch(r"[0-9a-fA-F]{32}", app_id):
            raise ValueError("invalid_provider_credentials")
        self._app_id = app_id
        self._transport = transport

    async def fetch_observations(self, *, currencies: Sequence[str], dates: Sequence[date]) -> Sequence[FxObservation]:
        quotes, days = validate_request(currencies, dates)
        result = []
        # Header authentication keeps credentials out of URLs and HTTPX request logs.
        # Redirects are never followed. No API call uses a paid base/symbols option.
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, transport=self._transport,
                                     headers={"Authorization": f"Token {self._app_id}"}) as client:
            for day in days:
                content = await self._fetch_day(client, day)
                result.extend(_parse(content, day, quotes))
        return result

    async def _fetch_day(self, client: httpx.AsyncClient, day: date) -> bytes:
        for attempt in range(3):
            delay = 0.5 * 2 ** attempt
            try:
                response = await asyncio.wait_for(client.get(
                    f"https://openexchangerates.org/api/historical/{day.isoformat()}.json"), timeout=15)
            except (httpx.TransportError, TimeoutError):
                code = "provider_transport_failure"
            else:
                status = response.status_code
                if status == 200:
                    if len(response.content) > 2_000_000:
                        raise ProviderError("provider_payload_too_large")
                    return response.content
                if status in (401, 403):
                    raise ProviderError("provider_authentication_failed")
                if status != 429 and not 500 <= status <= 599:
                    raise ProviderError("provider_request_rejected")
                code = "provider_rate_limited" if status == 429 else "provider_unavailable"
                retry_after = response.headers.get("Retry-After", "")
                if retry_after.isascii() and retry_after.isdigit() and len(retry_after) <= 6:
                    delay = min(5, max(delay, int(retry_after)))
            if attempt == 2:
                raise ProviderError(code) from None
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")
