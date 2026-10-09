"""Dated Fawaz Ahmed USD snapshots from jsDelivr; never fall back to latest."""
import json
from collections.abc import Sequence
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx

from app.schemas.fx import FxObservation
from app.services.fx_providers.common import (
    ProviderError, _invalid_constant, _unique_object, fetch_bytes, validate_request,
)

SOURCE = "fawazahmed0:historical:USD"


def _parse(content: bytes, day: date, quotes: list[str]) -> list[FxObservation]:
    try:
        data = json.loads(content, parse_float=Decimal, object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
        if not isinstance(data, dict) or data.get("date") != day.isoformat():
            raise ValueError("wrong_observation_date")
        rates = data["usd"]
        if not isinstance(rates, dict) or type(rates.get("usd")) not in (int, Decimal) or rates["usd"] != 1:
            raise ValueError("invalid_usd_base")
        fetched = datetime.now(timezone.utc)
        result = []
        for code in quotes:
            if code.lower() not in rates:
                continue  # The orchestrator rejects missing quotes before any write.
            rate = rates[code.lower()]
            if type(rate) is int:
                rate = Decimal(rate)
            result.append(FxObservation(base_currency="USD", quote_currency=code, rate=rate,
                as_of_date=day, source=SOURCE, fetched_at=fetched))
        return result
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ProviderError("invalid_provider_payload") from None


class FawazProvider:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    async def fetch_observations(self, *, currencies: Sequence[str], dates: Sequence[date]) -> Sequence[FxObservation]:
        quotes, days = validate_request(currencies, dates)
        result = []
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, transport=self._transport,
                                     headers={"Accept-Encoding": "identity"}) as client:
            for day in days:
                url = f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{day.isoformat()}/v1/currencies/usd.min.json"
                result.extend(_parse(await fetch_bytes(client, url), day, quotes))
        return result
