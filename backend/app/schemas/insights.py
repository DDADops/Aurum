from pydantic import BaseModel

from app.schemas.money import FinancialResponseStatus


class FinancialAlert(BaseModel):
    key: str
    severity: str
    # Machine-readable params for the frontend to interpolate into its own
    # localized message template (keyed by `key`) — keeps alert copy out of
    # the backend so it can be translated without an API change.
    params: dict[str, int]


class AlertsResponse(FinancialResponseStatus):
    alerts: list[FinancialAlert]
