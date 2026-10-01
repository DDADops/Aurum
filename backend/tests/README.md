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
both source and destination references. The original guard test uses a
cooperative writer; `test_native_transfers.py` now exercises real HTTP create,
bulk, PATCH, DELETE and recurring posting against concurrent currency changes.
Asset PATCH locks Asset before checking valuations and crypto transactions;
the valuation/crypto writer protocol and quote metadata remain later work.

Recurring snapshots remain unchanged by Account PATCH. The supported posting
path joins transaction Account locks and native amount validation. It refuses
concurrently edited templates with a retryable conflict (NOWAIT avoids inverse
lock order), and retains the transitional refusal for stale snapshots and
transfers requiring the later recurring implementation (`409 recurring_currency_revalidation_required`).
No automatic FX or full recurring revalidation is implemented here. Budget/Goal
reads expose native currencies; currency-aware budget aggregation remains later
work. Budget currency edits require an explicit replacement limit; Goal currency
edits require a replacement target and no contributions. Goal contribution
writes share the Goal row lock.

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


## Native transfer writers

New transfers require existing, distinct source/destination accounts. Under
Account locks, same-currency destination amounts normalize to source amount
(unequal explicit amounts fail); cross-currency transfers require an explicit
positive destination amount at scale 6. No settings currency or FX rate is used.
Non-transfers reject non-null transfer-only fields. Bulk locks the batch's whole
account union once and commits only after every item validates.

Financial PATCH fields are `type`, `account_id`, `transfer_account_id`, `amount`,
`transfer_amount`, `category_id`, `splits`, and `date`, based on field presence.
Validation uses the final state. Same-currency debit edits normalize the credit;
for an unchanged cross-currency pair, an existing authoritative credit remains
independent of a debit edit. Any account-pair change ending cross-currency
requires an explicit destination amount (also when the new account uses the
same currency as the old one). Transfer-to-income/expense clears omitted inherited
transfer fields but rejects explicit non-null ones. Legacy unresolved/missing
counterparties remain readable and allow notes/description/merchant/tag edits;
financial edits must reconcile the final state. Split equality remains exact.

All writers lock Accounts by ascending ID and refresh ORM state after waiting.
PATCH/DELETE discover old references, lock old/requested account union, then lock
and reload the Transaction. If its references changed, they return
`409 transaction_accounts_changed_retry` rather than add locks out of order.
DELETE holds locks through removal/commit. Account DELETE locks its own row and
returns `409 account_delete_requires_empty_history` for any source/destination
history; archive remains available. Existing CASCADE/SET NULL FKs are unchanged;
no migration was needed. Direct SQL and full backup restore are not ordinary
transaction writer endpoints and must not run concurrently with application
writers; versioned restore coordination remains backup-v2 work.

Transaction reads include both accounts, destination amount, completeness issues
and a derived Decimal `effective_rate` (80 significant digits for repeating
quotients). Unknown amounts/rates stay null. This rate is informational, not FX.
The only balance adjustment is native Account crediting using `transfer_amount`;
an unresolved incoming amount yields null balance with an issue, not zero or a
partial total. Source debit remains known. No account conversion is implemented.

Native Account balance readers are now finalized; see the audit below. FX,
full Net Worth conversion, crypto metadata, recurring reconciliation,
backup v2 and frontend transfer input remain separate commits.


## Dated reference FX foundation

`test_fx.py` covers exact direct observations, upserts, PostgreSQL constraints,
settings, historical lookup, semantic direct/inverse/bridge priority, independent
leg freshness, deterministic bridge selection, missing reasons and Decimal-only
conversion. Resolution permits at most two legs; it never fetches from a provider
or materializes derived observations. The unique pair/date B-tree also supports
backward date scans. Upsert replaces rate/source/fetched_at and leaves commit to
the caller. Providers implement only the future explicit sync/backfill protocol.

Stored observations are positive finite Numeric(38,18). Derived rates use 96
significant digits (ROUND_HALF_EVEN), including repeating inverses, with no scale
quantization. Final derived rates must remain within the positive storage range
[1e-18, 99999999999999999999.999999999999999999]; out-of-range paths are unusable,
never clamped. Intermediate legs are not range-clamped or money-rounded. Among
representable bridges, minimum maximum leg age wins, then bridge code ascending.
A fresh complete but unrepresentable route reports `precision_unrepresentable`;
otherwise a complete historical route with stale legs reports `stale`; otherwise
`absent`. Disconnected partial paths and future observations do not establish a
historical route. Same-currency resolution needs neither observations nor SQL.

Migration c4e8a91b720f follows a72e9b4c106d. Its roundtrip test explicitly expects
observation loss on downgrade; settings backfill to 7 on upgrade/re-upgrade.
Normal analytics/transaction GETs and reference resolution are tested with FX
provider fetching and HTTP network transport forbidden. Reference FX does not
supply execution transfer amounts or change the informational effective rate.

Aggregate conversion, sync/provider integration, frontend, recurring and crypto
currency semantics are deferred. Backup v1 is unchanged: it excludes FX rows and
fx_max_age_days. The dedicated backup-v2 commit must define their versioned
export/restore semantics; v1 is not a portable snapshot of this new FX state.


## Native Account balances and reader audit

`native_balance_service.native_legs` defines source income +amount, expense and
transfer -amount, and transfer destination +transfer_amount. A missing destination
produces only the known source debit. A NULL destination amount makes only that
existing account's balance unknown. `account_balances` shares this rule across
Account and idle-cash readers, summing with precision 96; balance, latest activity
and history derive from the same transaction query. Known zero is distinct from
None, including after deletion of the final transaction. Account list/POST/PATCH
expose native currency, balance, history and completeness; there is no individual
GET endpoint. Archive inclusion rules are unchanged. Unknown balances expose one
transfer_amount_unresolved issue, without a partial subtotal.

Reader classification (backend-wide transfer/amount/balance search):

- A, native: account_service and insights_service idle-cash now share the balance
  helper. Idle cash uses destination amounts, preserves latest activity and
  archive/type exclusions, and refuses unknown balances or history in a currency
  different from the threshold's settings currency. The public whole-history
  completeness guard suppresses those alerts; the local None result is also
  guarded with idle_cash_balance_unavailable if reached. Empty known zero has
  no historical currency input. FX threshold conversion/denomination is deferred.
- B, aggregate: net_worth_service cash events use shared native legs and retain
  None from the first unknown incoming event onward. capital_status restricts
  unknown-credit issues to included destination accounts. A known cross-currency
  credit is not an unresolved amount; currency mismatches still suppress mixed
  totals. A missing destination does not invalidate an existing source balance.
  The subsequent Net Worth FX stage below replaces its whole-history gate with
  dated valuation. Other consumers retain capital_status, including its archive
  scope.
- B, aggregate: Dashboard transferred_out sums source amounts intentionally;
  its metric-level currency guards remain. Cash Flow excludes transfers.
  Reports/category_rollup, Budget and Advice consume income/expense amounts or
  splits, never destination credits. Their base/native-budget FX aggregation
  belongs to later commits. Existing Dashboard/Reports/Cash Flow/Advice guards
  remain; Budget's previously deferred native-budget aggregation is not changed.
- C, unrelated: transaction/recurring writers, schemas and models, history/FK
  checks, backup v1 normalization, historical migrations and seed_mock_data are
  not balance readers. Same-currency writer/backfill copying of amount into
  transfer_amount is intentional; the mock seed bypasses normal writers and is
  unchanged. No remaining native reader credits a destination with source amount.

`test_native_balances.py` covers known/unknown/zero balances, both transfer sides,
multiple incoming transfers, missing destination, archive/history/delete behavior,
Account response consistency, cash events and account-type-scoped completeness,
idle-cash threshold safety and Decimal context independence. FX isolation is
checked both by forbidding provider/resolver/converter calls and by rejecting any
fx_rates SQL during Account requests; persisted arbitrary rates leave balances
unchanged. Native transfer writer and concurrency tests remain regression coverage.
No migration, frontend change or reference FX integration is part of this stage.


## Historical Net Worth through reference FX

Net Worth alone now consumes dated reference FX. _cash_cumulative_events returns
separate native cumulative histories by Account ID; it never combines currencies.
For each calendar point, native_legs reconstructs each included account's stock
through that date. Latest AssetValuation <= date is forward-filled in native
currency. Each stock is then converted using that point's date, not transaction
or original valuation date, and only converted amounts are summed. Future
transactions/valuations cannot affect today. Cash account types and archived
account inclusion are unchanged. Ranges 30d/90d/1y/5y/all remain; all starts at
the first applicable cash/valuation event, or today if none exist.

The Net-Worth-local FX cache keys source/target/date/max-age and caches failures
as well as successes across accounts, assets and today's role cash flows. It
uses resolve_rate/convert unchanged, with settings.fx_max_age_days. Exact native
zero skips resolution. No persistent cache, provider call or FX write exists.
At most one resolver call per nonzero currency/date is made within a request;
large ranges still perform one local lookup per distinct currency/date.

Each point has its own status. Unknown incoming transfer amounts invalidate the
owning account only from the transfer date; missing/stale/unrepresentable FX
invalidates only the affected valuation dates. No known subtotal is exposed.
Summary complete/missing_rates/data_issues describe TODAY, while historical-only
failures stay on points. Change metrics require complete first/final points and
carry their combined status in metric_status; a known zero starting denominator
retains the existing null change_percent without a data error.

Breakdown and risk monetary values use converted current components; allocation
percentages are computed from those values. Capital roles retain the asset-only
scope and convert both asset values and their static monthly_cash_flow in native
Asset.currency at today's FX. A missing flow-only rate suppresses capital_roles
with its own metric_status, without invalidating a known current stock. An
incomplete current stock suppresses breakdown/roles/risk rather than presenting
partial allocations. Their existing non-null item schemas are unchanged.
Monetary arithmetic uses Decimal with precision 96 and no quantization; existing
percentage response fields remain float only at the serialization boundary.

Assets with no valuation history retain valuation_missing. An asset with dated
history enters the chart at its first valuation (earlier contribution zero),
preserving the previous forward-fill semantics without using future values.
CryptoHolding-backed valuations remain valuation_currency_unconfirmed because
legacy quote provenance cannot be inferred; the crypto migration is deferred.
Native history is reconstructed from surviving records, not an audit log.

Net Worth no longer calls capital_status. Its pre-FX behavior for Insights is
unchanged; no Dashboard/Cash Flow/Reports/Budget/Goal/crypto/recurring/backup or
frontend conversion is included. No migration or converted-value persistence.

`test_net_worth_fx.py` verifies stock revaluation without new transactions,
point/current/change completeness, date bounds, weekend/staleness policy, zero
components, reconciliation, assets and converted allocations/roles, base changes
without native-data mutation, low ambient Decimal precision, request-cache query
counts (including missing results), and GET isolation from provider/HTTP/FX writes.
Native-balance internal-reader assertions now address per-account cash histories;
the public native Account contract remains unchanged.
