# Backend tests

Real integration tests against a real Postgres database — a dedicated
`aurum_test` database created fresh, migrated, and dropped again on every
run. They never touch the real `aurum` database or its data: the app's
`lifespan` startup (which seeds against the real DB) is never triggered, and
every route's DB dependency is overridden to point at `aurum_test` instead.

## Running

The stack must already be up (`docker compose up -d`). Test dependencies
aren't baked into the production image, so install them once per container
lifetime, then run pytest inside the running `backend` container — it's the
only place with network access to Postgres:

```bash
docker compose exec backend pip install --user -r requirements-dev.txt
docker compose exec backend python -m pytest -v
```

Re-run just the `python -m pytest -v` line for subsequent runs; the
`pip install` only needs repeating after a container restart/rebuild.

Two details that both come from the container running as an unprivileged user
(`aurum`, uid 10001 — see backend/Dockerfile) rather than root:

- `--user` on the install: site-packages belongs to root and isn't writable.
  The packages land in `/home/aurum/.local`, which isn't on `PATH`, hence
  `python -m pytest` instead of a bare `pytest`.
- pytest prints a `PytestCacheWarning` about not being able to write
  `/app/.pytest_cache`. Expected — the source tree is read-only to the app
  user by design. Tests still pass; add `-p no:cacheprovider` to silence it.

## Adding a test

- Use the `client` fixture (an `httpx.AsyncClient` wired to the app) to hit
  the API the same way the frontend does — prefer this over reaching into
  services/models directly, so tests keep verifying the actual contract.
- `account_id` and `categories` fixtures give you the same default
  account/categories a real fresh install seeds.
- Every table is truncated and reseeded before each test (see
  `conftest.py::_clean_database`), so tests don't need to worry about
  leftover state or guess at IDs from a previous test.

## Native currency persistence (commit 4)

`test_native_currency.py` runs against the temporary database above. It covers
legacy backfill, reversible downgrade and transactional preflight refusals,
dynamic history guards, currency normalization, snapshots and v1 backup fields.
The previous precision migration test inserts only legacy columns while the
previous schema is installed, and restores the current head before returning.

Account PATCH locks the Account row and then queries transaction history on
both source and destination references. The concurrency test uses a cooperative
writer holding that same lock. **Transaction create/PATCH/bulk and recurring
posting do not yet implement the complementary Account lock protocol**: commit 5
must lock all affected accounts in stable ID order before currency-dependent
validation and writes. Do not treat this intermediate commit as full race
protection. Asset PATCH similarly locks Asset before checking valuations and
crypto transactions; the valuation/crypto writer protocol and quote metadata
still need completion in the corresponding later work.

Recurring snapshots remain unchanged by Account PATCH. Until the recurring
posting implementation is replaced, posting refuses stale source snapshots,
missing/unresolved destinations and transfers the old writer cannot faithfully
represent (`409 recurring_currency_revalidation_required`). No automatic FX,
revalidation, transaction destination write behavior or balance rewrite is
implemented here. Budget/Goal reads expose native currencies; currency-aware
budget aggregation remains later work. Budget currency edits require an
explicit replacement limit; Goal currency edits require a replacement target
and no contributions. Goal contribution writes share the Goal row lock.

Asset currency changes require explicit replacement/clearing of existing
monthly cash flow (including zero). On an empty holding they clear `last_price`
and all five cached price-change fields. No quote currency metadata or sync
service changes are introduced; future price refresh validation belongs to the
crypto work. There is no valuation deletion API and no permanent currency lock.

Backup remains v1, with optional transfer/native currency fields as a transitional
compatibility mechanism for this development branch. New exports
include authoritative values; restore preserves them, including explicit null
unresolved amounts. Only omitted legacy fields are inferred from the backup's
accounts/settings using the migration rules. Files containing the extended v1
fields must be restored with this or a newer Aurum build; older readers can
ignore unknown fields. A versioned portability contract remains backup-v2 work.

The native-currency migration uses settings row `id=1`, falling back to its frozen
local `USD` default only when absent, independently of runtime configuration.
Downgrade locks all relevant tables and
refuses authoritative destination amounts incompatible with the old schema
(including cross-currency 1:1 amounts), native Budget/Goal currencies differing
from legacy settings, or recurring snapshots differing from their accounts.
Run schema migrations with application writers stopped, and recreate pooled
asyncpg connections after DDL to avoid stale prepared-statement metadata.
