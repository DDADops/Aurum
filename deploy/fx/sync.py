"""Host-only Compose launcher. Run via systemd; never import application code."""
import argparse
import fcntl
import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def emit(event, **values):
    print(json.dumps({"event": event, **values}, sort_keys=True), flush=True)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        emit("invalid_arguments", exit_code=2)
        raise SystemExit(2)


def completed_date(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value or not date(1999, 1, 1) <= parsed < datetime.now(timezone.utc).date():
        raise ValueError
    return parsed


def command(start, end, currencies, dry_run):
    args = ["docker", "compose", "--project-name", os.environ["AURUM_FX_COMPOSE_PROJECT"],
            "--env-file", ".env", "-f", "docker-compose.yml", "run", "--rm", "--no-deps",
            "--pull", "never", "-T", "--name", os.environ["AURUM_FX_COMPOSE_PROJECT"] + "-fx-sync",
            "-e", "AURUM_OXR_APP_ID", "backend", "python", "-m", "scripts.sync_fx",
            "--from", start.isoformat(), "--to", end.isoformat(), "--currencies", currencies]
    return args + (["--dry-run"] if dry_run else [])


def execute(args, state):
    currencies = os.environ["AURUM_FX_CURRENCIES"]
    if not re.fullmatch(r"[A-Z]{3}(,[A-Z]{3})*", currencies):
        raise ValueError
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", os.environ["AURUM_FX_COMPOSE_PROJECT"]):
        raise ValueError
    if not re.fullmatch(r"[a-fA-F0-9]{32}", os.environ.get("AURUM_OXR_APP_ID", "")):
        raise ValueError
    cursor = state / "cursor.json"
    manual = args.start is not None or args.end is not None
    if manual:
        start, end = completed_date(args.start), completed_date(args.end)
    else:
        start = completed_date(os.environ["AURUM_FX_START_DATE"])
        if cursor.exists():
            saved = json.loads(cursor.read_text())
            if saved["currencies"] != currencies:
                raise ValueError
            start = completed_date(saved["through"]) + timedelta(days=1)
        end = min(datetime.now(timezone.utc).date() - timedelta(days=1), start + timedelta(days=30))
        if start > end:
            emit("up_to_date")
            return 0
    if not 1 <= (end - start).days + 1 <= 31:
        raise ValueError
    # Never relay Docker diagnostics: interpolation/daemon errors may contain secrets.
    result = subprocess.run(command(start, end, currencies, args.dry_run),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    # Only the known CLI JSON report is relayed, never arbitrary container output.
    try:
        report = json.loads(result.stdout)
        counts = {key: report[key] for key in (
            "observations_inserted", "observations_to_insert", "identical_observations",
            "conflicting_observations", "missing_observations")}
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError
        if report["exit_code"] != result.returncode or result.returncode not in range(7):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        emit("compose_execution_failed", exit_code=result.returncode)
        return result.returncode if result.returncode > 0 else 70
    emit("ingestion", start=start.isoformat(), end=end.isoformat(), dry_run=args.dry_run,
         status=("success", "execution_failure", "invalid_configuration", "provider_or_batch_failure",
                 "missing_observations", "conflicts", "database_or_runtime_failure")[result.returncode],
         exit_code=result.returncode, **counts)
    if result.returncode == 0 and not manual and not args.dry_run:
        temporary = state / "cursor.tmp"
        temporary.write_text(json.dumps({"through": end.isoformat(), "currencies": currencies}) + "\n")
        temporary.replace(cursor)
    return result.returncode


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        state = Path(os.environ.get("AURUM_FX_STATE_DIR", "/var/lib/aurum-fx"))
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (state / "run.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                emit("execution_locked", exit_code=75)
                return 75
            if (state / "restore-in-progress").exists():
                emit("restore_in_progress", exit_code=75)
                return 75
            return execute(args, state)
    except (ValueError, KeyError, TypeError):
        emit("invalid_configuration", exit_code=2)
        return 2
    except OSError:
        emit("host_execution_failed", exit_code=70)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
