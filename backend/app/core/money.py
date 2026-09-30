"""Decimal input bounds. Signs and zero rules belong to domain schemas."""
from decimal import Decimal, localcontext
from typing import Annotated, Iterable

from pydantic import Field

MONEY_PRECISION = 20
MONEY_SCALE = 6
VALUATION_PRECISION = 38
VALUATION_SCALE = 18

Money = Annotated[Decimal, Field(max_digits=MONEY_PRECISION, decimal_places=MONEY_SCALE, allow_inf_nan=False)]
Valuation = Annotated[Decimal, Field(max_digits=VALUATION_PRECISION, decimal_places=VALUATION_SCALE, allow_inf_nan=False)]


def sum_money(values: Iterable[Decimal]) -> Decimal:
    """Keep split equality independent of the caller's Decimal context."""
    with localcontext() as context:
        context.prec = 96
        return sum(values, Decimal("0"))


def effective_transfer_rate(amount: Decimal, transfer_amount: Decimal | None) -> Decimal | None:
    """Informational destination/source ratio, never a reference FX quote.

    Non-terminating quotients use 80 significant digits; stored amounts remain
    authoritative and are never reconstructed from this display ratio.
    """
    if transfer_amount is None or not amount.is_finite() or not transfer_amount.is_finite():
        return None
    if amount <= 0 or transfer_amount <= 0:
        return None
    with localcontext() as context:
        context.prec = 80
        return transfer_amount / amount
