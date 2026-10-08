# Scheduled reference FX ingestion

This is an opt-in Linux host integration, not a deployment script. It uses the
existing backend image for short-lived containers. No worker, migrations, API
startup hooks or network calls from GET requests are added. The resolver and
conflict-rejecting ingestion policy are unchanged.

## Prerequisites and installation

- Linux with systemd, Python 3.10+, Docker Engine and Compose v2 (`docker compose`).
- Existing checkout at `/opt/aurum`, its built backend image, running database,
  and migrations already applied by the normal deployment procedure.
- The existing Compose project name must be known. A wrong project can select a
  different network/database/image. Verify it with `docker compose ls`.
- These units assume the base `docker-compose.yml` and `/opt/aurum/.env`. If your
  deployment changes database/network configuration in overrides, adapt the
  wrapper's Compose file list before installation and test against that stack.
- Accurate host UTC clock and outbound HTTPS access to Open Exchange Rates.
  Confirm your API plan permits historical requests and your quota is sufficient.

Install configuration (as root):

```sh
install -d -m 0700 /etc/aurum /var/lib/aurum-fx
install -m 0600 /opt/aurum/deploy/fx/fx.env.example /etc/aurum/fx.env
```

Edit `/etc/aurum/fx.env` with a local editor. Set `AURUM_OXR_APP_ID`, the existing
Compose project name and a deliberate historical start date. Keep the initial
currency list `RUB,EUR,VND,CNY`. USD is the provider base, not a requested quote.
The start date must precede today in UTC. The real file must remain root:root,
0600; its directory 0700. Protect `/opt/aurum/.env` (database credentials) with
0600 as well. Keep the checkout and scripts root-owned and not writable by
untrusted users: the host service runs as root to access Docker. Docker access
is effectively root access.

The API key is loaded by `EnvironmentFile`, then inherited by Compose using
`-e AURUM_OXR_APP_ID` **without a value in argv**. It is not installed in the
normal backend service environment. Docker administrators can inspect container
environment variables; this does not protect against a privileged host user.
Never use shell tracing (`set -x`), echo the key, put it on a command line, or
publish environment/container inspection output. The wrapper suppresses Docker
stderr and only emits whitelisted structured CLI counters/statuses, not raw
provider errors, bodies, URLs or arbitrary process output.

Install units, but do not enable the timer before the first dry-run/write:

```sh
install -m 0644 /opt/aurum/deploy/fx/aurum-fx.service /etc/systemd/system/
install -m 0644 /opt/aurum/deploy/fx/aurum-fx.timer /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/aurum-fx.service /etc/systemd/system/aurum-fx.timer
systemctl daemon-reload
```

For a different installation path, adjust both `WorkingDirectory` and
`ExecStart` with a systemd override. Keep the default state directory shared
between scheduled, manual and restore operations.

## Initial dry-run, backfill and first write

In a root shell, with tracing disabled, load the protected environment:

```sh
cd /opt/aurum
set -a
. /etc/aurum/fx.env
set +a
python3 deploy/fx/sync.py --from 2020-01-01 --to 2020-01-07 --dry-run
```

Replace example dates with your intended completed UTC interval. Review JSON
counts and exit status. Dry-run fetches provider data and reads the database,
but neither writes rates nor advances the cursor. Before the first write,
export a Backup v2 (which includes persisted FX rates) or take a database
snapshot. Run the same range without `--dry-run` for an explicit manual write.

Manual ranges are inclusive, at most 31 days, and do not advance the scheduled
cursor. For large initial backfills use consecutive <=31-day intervals. Never
invent rates for dates/currencies the provider cannot supply. Missing data
rejects the entire batch. No forward-fill or automatic overwrite is performed.

To dry-run the first scheduled batch, run `python3 deploy/fx/sync.py --dry-run`.
Then `systemctl start aurum-fx.service` performs the first scheduled write.
Inspect its status/logs and `/var/lib/aurum-fx/cursor.json`, then enable:

```sh
systemctl enable --now aurum-fx.timer
```

The underlying Compose command is equivalent to:

```sh
docker compose --project-name "$AURUM_FX_COMPOSE_PROJECT" --env-file .env -f docker-compose.yml run --rm --no-deps --pull never -T -e AURUM_OXR_APP_ID backend python -m scripts.sync_fx --from 2020-01-01 --to 2020-01-07 --currencies RUB,EUR,VND,CNY --dry-run
```

Use the **wrapper** operationally: direct Compose commands bypass the host
lock, maintenance marker and cursor. The image has no ENTRYPOINT and `run`
replaces its normal CMD, so this runs neither Alembic nor Uvicorn. `--no-deps`
also prevents starting the database automatically. Normal `compose up` startup
remains unchanged.

## Daily operation and missed dates

The timer triggers at 03:15 UTC with up to five minutes jitter. Only dates up to
yesterday UTC are submitted. `Persistent=true` triggers a run after downtime;
the cursor, rather than systemd, recovers each missed date. Each run handles at
most 31 days beginning after the last successful batch. An outage longer than
31 days needs repeated `systemctl start aurum-fx.service` calls after each run
finishes (or successive daily runs). The cursor advances atomically only after
a successful non-dry scheduled batch; failed dates are retried on the next run.
A crash after DB commit but before cursor update safely repeats an identical
batch. Do not move the cursor forward manually to hide a missing date.

One systemd service cannot run twice simultaneously. In addition, all wrapper
runs hold a nonblocking `flock` on `/var/lib/aurum-fx/run.lock`; contention exits
75. A fixed per-project container name also prevents a replacement container
if a previous run survived host/client interruption. If this occurs, inspect
and stop the remaining container before retrying; never delete the lock file.
Do not force-kill a healthy ingestion. The existing database table lock remains
the final transactional protection against conflicting writes/restore.

Changing the currency list requires explicit backfill planning: the saved
cursor records the list and refuses to advance with a different list. With
the timer disabled and no active run, archive the cursor, configure a suitable
start date, and rerun the expanded list. Existing identical observations remain
unchanged. Preserve cursor files with database backups; after a restore reset
the cursor to a safe earlier start if the restored data predates it.

Each requested day costs one historical API request (plus up to two retries
for transient errors), independent of the four-currency selection. Dry-runs
and repeated manual ranges also consume quota. A 31-day batch may cost up to
93 requests. No automatic retry loop or unlimited startup catch-up is installed.
Provider delays, missing VND/RUB observations or plan restrictions require
operator action; do not resolve conflicts by overwriting stored history.

```sh
systemctl list-timers aurum-fx.timer
systemctl status aurum-fx.service
systemctl show aurum-fx.service -p Result -p ExecMainStatus
journalctl -u aurum-fx.service --since yesterday
```

Structured results include requested start/end, dry-run flag, inserted/planned,
identical/conflicting/missing counters and status. CLI exit codes are preserved:

| Code | Meaning |
| --- | --- |
| 0 | Success or already current |
| 2 | Invalid arguments/configuration/credentials |
| 3 | Provider or batch validation failure |
| 4 | Missing observations |
| 5 | Existing rate or source conflict; no batch writes |
| 6 | Database/runtime failure; batch rolled back |
| 70 | Host execution/report failure |
| 75 | Another run or restore maintenance blocks execution |

Other Docker exit codes propagate as `compose_execution_failed`. The journal
intentionally omits raw Docker diagnostics; check daemon health/image/network
locally without exposing secrets. Failures mark the service failed; there is no
automatic alert delivery, so configure host monitoring for failed units.

## Disable, restore coordination and rollback

Disable future runs with `systemctl disable --now aurum-fx.timer`. This does
not interrupt an active oneshot: wait for it to finish. Before **any** UI/API or
database restore, enter maintenance in a root shell:

```sh
systemctl disable --now aurum-fx.timer
touch /var/lib/aurum-fx/restore-in-progress
exec 9>/var/lib/aurum-fx/run.lock
flock -x 9
```

Acquiring this lock waits for any active wrapper to finish. Keep this shell and
lock open throughout restore. The persistent marker blocks later runs even
after a crash/reboot. Verify no orphan `<project>-fx-sync` container is still
running. Now perform the restore using the existing supported procedure.
After validating the restored database, reconcile/archive the cursor as
described above, remove the marker, release the lock, and optionally re-enable:

```sh
rm /var/lib/aurum-fx/restore-in-progress
flock -u 9
exec 9>&-
systemctl enable --now aurum-fx.timer
```

This is an operator maintenance protocol, not an API restore interlock. Every
restore operator must follow it. Uncoordinated API restores still serialize DB
writes through existing PostgreSQL locks, but cannot stop provider fetches or
prevent a later ingestion from adding rates after restore. Direct CLI/other
hosts also bypass the host lock: use only one scheduler for a database.

To roll back scheduling, disable the timer, wait for the service, remove the
units and reload systemd. Rates already committed remain unchanged. Do not
blindly delete rates; rollback of data requires a reviewed backup restore under
maintenance (and may also restore other financial data). No schema rollback is
needed. This integration does not deploy itself or run on the VPS automatically.

## Verification

```sh
python3 -m unittest discover -s deploy/fx -v
python3 -m compileall -q deploy/fx
git diff --check
```

Tests cover command construction, startup isolation, redaction, real file locks,
maintenance, cursor recovery, date boundaries and exit propagation using mocked
Compose execution. Live Docker/systemd operation must be checked on the target
Linux host before enabling the timer.

References: [Compose run semantics](https://docs.docker.com/reference/cli/docker/compose/run/)
and [systemd timer semantics](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html).

### Wrapper input/output bounds

Persisted cursors are validated separately from ingestion request dates. The
cursor must be a JSON object containing only `through` (canonical ISO date,
1999-01-01 through yesterday UTC) and `currencies` (unique uppercase quote codes,
matching the configured list). Duplicate keys, corrupt/oversized JSON, current
or future dates are rejected with `invalid_cursor` (exit 2), without resetting
or advancing state. Cursor files are limited to 4 KiB.

Compose stdout is read incrementally, with a 64 KiB limit and a one-byte
oversize probe. Excess output produces `compose_output_limit_exceeded` (exit 70)
and no cursor update. The child is terminated and reaped; after two seconds
without termination it is killed and reaped. Raw output is never logged.
Valid CLI reports retain their exit codes; malformed successful reports fail
with exit 70. Docker-managed containers may outlive a killed Compose client;
use the existing orphan-container check before retrying. Scheduling and restore
maintenance coordination are unchanged.
