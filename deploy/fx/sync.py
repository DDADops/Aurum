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


MAX_STDOUT_BYTES = 64 * 1024


class InvalidCursor(ValueError):
    pass


class OutputLimitExceeded(Exception):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def validate_currencies(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}(,[A-Z]{3})*", value):
        raise ValueError
    codes = value.split(",")
    if "USD" in codes or len(codes) != len(set(codes)):
        raise ValueError


def load_cursor(path, currencies, provider):
    # Persisted state has its own schema; it is not a new ingestion request.
    try:
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError
        saved = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(saved, dict) or set(saved) not in ({"through", "currencies"}, {"through", "currencies", "provider"}):
            raise ValueError
        if saved.get("provider", "oxr") != provider:
            raise ValueError
        validate_currencies(saved["currencies"])
        if saved["currencies"] != currencies or not isinstance(saved["through"], str):
            raise ValueError
        through = date.fromisoformat(saved["through"])
        if (through.isoformat() != saved["through"] or
                not date(1999, 1, 1) <= through < datetime.now(timezone.utc).date()):
            raise ValueError
        return through
    except (ValueError, TypeError, RecursionError):
        raise InvalidCursor from None


def capture_child(argv):
    # Unbuffered binary reads retain at most 64 KiB plus a one-byte limit probe.
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          bufsize=0) as child:
        content = bytearray()
        try:
            while True:
                chunk = child.stdout.read(min(8192, MAX_STDOUT_BYTES - len(content) + 1))
                if not chunk:
                    break
                if len(chunk) > MAX_STDOUT_BYTES - len(content):
                    raise OutputLimitExceeded
                content.extend(chunk)
            return subprocess.CompletedProcess(argv, child.wait(), bytes(content))
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


def command(start, end, currencies, dry_run):
    provider = os.environ.get("AURUM_FX_PROVIDER", "oxr")
    args = ["docker", "compose", "--project-name", os.environ["AURUM_FX_COMPOSE_PROJECT"],
            "--env-file", ".env", "-f", "docker-compose.yml", "run", "--rm", "--no-deps",
            "--pull", "never", "-T", "--name", os.environ["AURUM_FX_COMPOSE_PROJECT"] + "-fx-sync",
            "-e", "AURUM_FX_PROVIDER=" + provider,
            *(["-e", "AURUM_OXR_APP_ID"] if provider == "oxr" else []),
            "backend", "python", "-m", "scripts.sync_fx",
            "--from", start.isoformat(), "--to", end.isoformat(), "--currencies", currencies]
    return args + (["--dry-run"] if dry_run else [])


def execute(args, state):
    provider = os.environ.get("AURUM_FX_PROVIDER", "oxr")
    if provider not in ("oxr", "fawaz"):
        raise ValueError
    currencies = os.environ["AURUM_FX_CURRENCIES"]
    validate_currencies(currencies)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", os.environ["AURUM_FX_COMPOSE_PROJECT"]):
        raise ValueError
    if provider == "oxr" and not re.fullmatch(r"[a-fA-F0-9]{32}", os.environ.get("AURUM_OXR_APP_ID", "")):
        raise ValueError
    cursor = state / "cursor.json"
    manual = args.start is not None or args.end is not None
    if manual:
        start, end = completed_date(args.start), completed_date(args.end)
    else:
        start = completed_date(os.environ["AURUM_FX_START_DATE"])
        if cursor.exists():
            start = load_cursor(cursor, currencies, provider) + timedelta(days=1)
        end = min(datetime.now(timezone.utc).date() - timedelta(days=1), start + timedelta(days=30))
        if start > end:
            emit("up_to_date")
            return 0
    if not 1 <= (end - start).days + 1 <= 31:
        raise ValueError
    # Never relay Docker diagnostics: interpolation/daemon errors may contain secrets.
    result = capture_child(command(start, end, currencies, args.dry_run))
    # Only the known CLI JSON report is relayed, never arbitrary container output.
    try:
        report = json.loads(result.stdout, object_pairs_hook=unique_object)
        counts = {key: report[key] for key in (
            "observations_inserted", "observations_to_insert", "identical_observations",
            "conflicting_observations", "missing_observations")}
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ValueError
        if (type(report["exit_code"]) is not int or report["exit_code"] != result.returncode
                or result.returncode not in range(7)):
            raise ValueError
    except (ValueError, KeyError, TypeError, RecursionError):
        emit("compose_execution_failed", exit_code=result.returncode)
        return result.returncode if result.returncode > 0 else 70
    emit("ingestion", start=start.isoformat(), end=end.isoformat(), dry_run=args.dry_run,
         status=("success", "execution_failure", "invalid_configuration", "provider_or_batch_failure",
                 "missing_observations", "conflicts", "database_or_runtime_failure")[result.returncode],
         exit_code=result.returncode, **counts)
    if result.returncode == 0 and not manual and not args.dry_run:
        temporary = state / "cursor.tmp"
        temporary.write_text(json.dumps({"through": end.isoformat(), "currencies": currencies, "provider": provider}) + "\n")
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
    except InvalidCursor:
        emit("invalid_cursor", exit_code=2)
        return 2
    except OutputLimitExceeded:
        emit("compose_output_limit_exceeded", exit_code=70)
        return 70
    except (ValueError, KeyError, TypeError):
        emit("invalid_configuration", exit_code=2)
        return 2
    except OSError:
        emit("host_execution_failed", exit_code=70)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
