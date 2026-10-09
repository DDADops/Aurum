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

from app.services.fx_providers.common import (
    MAX_DAYS, MAX_RESPONSE_BYTES, ProviderError, validate_request,
    _unique_object, _invalid_constant, fetch_bytes,
)
from app.schemas.fx import FxObservation

SOURCE = "openexchangerates:historical:USD"
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
                                     headers={"Authorization": f"Token {self._app_id}", "Accept-Encoding": "identity"}) as client:
            for day in days:
                content = await fetch_bytes(client, f"https://openexchangerates.org/api/historical/{day.isoformat()}.json")
                result.extend(_parse(content, day, quotes))
        return result
