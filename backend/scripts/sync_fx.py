"""Explicit historical FX ingestion. See tests/README.md for usage and exit codes."""
import argparse
import asyncio
import json
import os
from datetime import date, timedelta

from app.core.currency import SUPPORTED_CURRENCIES
from app.services.fx_ingestion_service import IngestionReport, ingest_rates
from app.services.fx_providers.openexchangerates import MAX_DAYS, SOURCE, OpenExchangeRatesProvider, validate_request
from app.services.fx_providers.fawaz import FawazProvider, SOURCE as FAWAZ_SOURCE


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo unknown CLI arguments: they can contain accidental secrets.
        raise ValueError("invalid_cli_arguments")


def parse_request(argv):
    parser = SafeParser(description="Load completed UTC daily USD reference rates (maximum 31 days).")
    parser.add_argument("--from", dest="start", required=True)
    parser.add_argument("--to", dest="end", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--currencies", default=",".join(sorted(SUPPORTED_CURRENCIES - {"USD"})))
    args = parser.parse_args(argv)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if args.start != start.isoformat() or args.end != end.isoformat():
        raise ValueError("invalid_date_format")
    count = (end - start).days + 1
    if not 1 <= count <= MAX_DAYS:
        raise ValueError("invalid_date_range")
    dates = [start + timedelta(days=i) for i in range(count)]
    currencies, dates = validate_request(args.currencies.split(","), dates)
    return currencies, dates, args.dry_run


async def run_cli(argv=None) -> int:
    report = IngestionReport([], False)
    try:
        currencies, dates, dry_run = parse_request(argv)
        report = IngestionReport([day.isoformat() for day in dates], dry_run)
        selection = os.environ.get("AURUM_FX_PROVIDER", "oxr")
        if selection == "oxr":
            provider = OpenExchangeRatesProvider(os.environ.get("AURUM_OXR_APP_ID", ""))
            source = SOURCE
        elif selection == "fawaz":
            provider = FawazProvider()
            source = FAWAZ_SOURCE
        else:
            raise ValueError("invalid_provider")
    except ValueError:
        report.errors = ["invalid_arguments_or_missing_credentials"]
        report.exit_code = 2
    else:
        try:
            # No application lifespan/startup, seeds or migrations are invoked.
            from app.db.session import AsyncSessionLocal, engine
            try:
                report = await ingest_rates(provider, AsyncSessionLocal, currencies=currencies,
                                            dates=dates, source=source, dry_run=dry_run)
            finally:
                await engine.dispose()
        except Exception:
            report.errors = ["ingestion_configuration_or_runtime_failure"]
            report.exit_code = 6
    print(json.dumps(report.as_dict(), sort_keys=True))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_cli()))
