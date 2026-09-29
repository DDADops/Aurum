"""Supported native currencies: the UI's curated list plus VND."""
from typing import Annotated

from pydantic import AfterValidator, BeforeValidator, Field

SUPPORTED_CURRENCIES = frozenset(
    "AED AMD AUD AZN BRL BYN CAD CHF CNY CZK EUR GBP GEL HKD HUF IDR ILS INR "
    "JPY KGS KRW KZT MDL MXN NZD PLN RON RUB SAR SGD THB TJS TMT TRY UAH USD UZS VND ZAR".split()
)


def normalize_currency(value: str) -> str:
    if not isinstance(value, str) or value.upper() not in SUPPORTED_CURRENCIES:
        raise ValueError("Unsupported currency code")
    return value.upper()


CurrencyCode = Annotated[
    str, BeforeValidator(normalize_currency), Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
]
# PATCH omission is allowed; an explicit null cannot clear a NOT NULL currency.
CurrencyUpdate = Annotated[CurrencyCode | None, AfterValidator(normalize_currency)]
