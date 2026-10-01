"""Future explicit sync/backfill boundary; ordinary reads never fetch FX."""
from collections.abc import Sequence
from datetime import date
from typing import Protocol

from app.schemas.fx import FxObservation


class FxProvider(Protocol):
    async def fetch_observations(
        self, *, currencies: Sequence[str], dates: Sequence[date],
    ) -> Sequence[FxObservation]:
        """Return direct observations with Decimal rates and explicit provenance."""
        ...
