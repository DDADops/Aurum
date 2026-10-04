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


## Dashboard dated flow aggregation

Dashboard consumes local reference FX independently of Net Worth. Monetary
metric classification: real_income, spent and transferred_out are transaction
flows (A); net is income minus expense (D); spending_by_category amounts/children
are grouped expense flows (A), with shares derived from converted spent (D).
There are no stock metrics (B), no backend trend/comparison series, and no
transaction count metric. Year/month and category identity/style fields are
non-monetary (C). The frontend savings-rate display derives from income/net (D).
RecentTransactionsCard requests the separate transaction API and retains native
amount/account.currency; Dashboard does not embed or rewrite transaction records.

Each qualifying source amount is converted on Transaction.date before addition,
using settings.currency and fx_max_age_days. Month boundaries remain inclusive
first/last calendar dates, including any future-dated records within that month.
Future FX observations never change an earlier flow. Transfers contribute only
the source reference-valued transferred_out volume, never income/expense; there
is no destination-volume metric. Execution rates/destination amounts are not FX.

A small Dashboard-local cache stores both ResolvedRate and MissingRate by
source/target/date/max-age. No private Net Worth helpers are imported. Zero skips
FX lookup. Monetary operations use local precision 96 without quantization;
repeating inverse rates retain the existing FX layer's finite precision. The
last stored split receives the Decimal-context residual from the converted
transaction total, preserving reconciliation without cents rounding. Null-category
allocations remain excluded from category slices but included in spent, as before.
Parent/child grouping, sorted top-eight slices and Other are unchanged. A pure
rollup_category_contributions helper is shared with the old Reports loader;
Reports FX behavior is deliberately unchanged.

Income, expense and outgoing-volume statuses remain independent. Missing/stale/
precision_unrepresentable results null only the affected metric. Net depends on
income and expense; category distribution depends on complete expense (including
uncategorized expense), and is empty/unavailable if incomplete. Top-level status
merges all metric statuses, as before. Empty periods are complete known zeros.
Dashboard no longer uses the pre-FX transaction_status gate; other consumers and
financial_data_service are unchanged.

Direct-caller safety: Insights returns an unknown negative-flow streak and
suppresses that alert when Dashboard net is unavailable; Advice suppresses its
savings-rate comparison when either period lacks income/net. Their existing
pre-FX public guards remain. Advice raw category-expense sums and Budget-based
alerts are intentionally deferred, not newly enabled for mixed currencies.
Remaining sums in Dashboard are converted flows/categories; Insights sums risk
percentages and over-budget counts (non-monetary). No destination-credit/source
amount substitution or NULL-to-zero fallback remains in Dashboard/caller handling.

`test_dashboard_fx.py` covers dated 100000 RUB flows at USD/RUB 100 and 80 =>
2250 USD, future-rate isolation, category/split conversion, independent failures,
transfer semantics, zero legacy input, native-data-preserving base changes,
low ambient precision, caller suppression and request-cache query counts.
Provider/HTTP transport calls and FX writes are forbidden during tested GETs.
No migration, frontend change, persistent conversion or sync/backfill is added.
Cash Flow, Reports, Budget, Goal, Recurring, Crypto, backup v2 and full
Insights/Advice currency semantics remain separate work.

### Cash Flow dated reference FX

`test_cash_flow_fx.py` covers transaction-date conversion before monthly grouping.
Income/expense and their totals are flows; net and total_net are derived flows;
dates/year/month are non-monetary. There are no stock metrics or embedded native
transaction details. Inclusive ranges, fallback bounds, future transactions,
chronological months and empty buckets retain their existing semantics.

Each source Transaction.amount uses Account.currency and Transaction.date with
the existing resolver/convert functions, settings.currency and fx_max_age_days.
Totals sum the same converted bucket metrics. Decimal precision is 96 without
quantization, including the resolver's finite repeating inverse precision.
Transfers are excluded from both bounds and contributions; transfer_amount and
execution rates never enter the report. Exact zero skips FX.

Each point exposes FinancialDataStatus and income/expense/net metric_status;
response metric_status contains total_income/total_expense/total_net. Missing,
stale or precision_unrepresentable FX nulls only dependent metrics and totals,
never exposes partial totals, and preserves other months and known metrics.
Top-level status merges total statuses. Empty buckets are complete zeros.
Missing metadata is deduplicated without collapsing different requested dates.

A small Cash-Flow-local cache stores successes and failures by source/target/
date/max-age for one request. Shared-helper extraction is deferred to avoid
changing Dashboard or importing private helpers. Tests verify dated rates,
range boundaries, independent failures, low ambient precision, cache query
counts, base-currency recomputation and unchanged account/transaction/FX rows.
Provider and HTTP calls are forbidden, and FX statements must be SELECT only.

Search audit: the only direct backend caller is the route. No raw monetary sum,
today/bucket FX date, transfer amount or NULL-to-zero fallback remains in this
path; sums consume converted points, and SQL min/max only computes date bounds.
The existing frontend guards top-level incompleteness before reading points;
nullable point types and partial-series rendering await frontend integration.
No migration or frontend changes. Reports, Budget, Goal, Recurring, Crypto,
backup v2 and full Insights/Advice FX integration remain deferred.

### Reports dated reference FX

Reports exposes two endpoints only: category spending detail and category
ranking by income/expense kind. There are no period comparisons, net/savings,
stock metrics, account filters or embedded native transaction details.

Field inventory and dependencies:

| Output | Source and aggregation | Completeness |
| --- | --- | --- |
| detail series[].amount | Transaction.amount in Account.currency, FX on Transaction.date, selected category/split allocations grouped by month | Each month independently |
| detail total_amount | Sum of converted monthly points | All selected contributions |
| detail average_per_month | Converted total divided by number of series months, including empty months | Total must be complete |
| ranking items[].amount and children[].amount | Dated converted category allocations, then parent/child rollup | Entire qualifying categorized set must be complete |
| ranking total_amount | Sum of converted ranked categories | Same set as items |
| ranking items[].percent | Converted item / converted total, Decimal ratio then existing float wire field | Same set as items |

Amounts/totals are transaction flows; average and percentage are derived flows.
Dates, category metadata and distinct transaction counts are non-monetary.
Average retains the existing denominator but no longer quantizes to cents.
Monetary arithmetic uses local precision 96; repeating inverse precision is
inherited from the resolver, with no per-transaction/split/month quantization.

The loader filters kind, inclusive dates and qualifying categories before FX.
Parent detail includes children; child detail includes only that child. It
converts the full transaction, allocates all stored splits in ID order with
the same final residual as Dashboard, then selects report categories. Deleted
category allocations participate in reconciliation but stay outside reports;
uncategorized records are excluded, as before. Ranking uses the existing pure
rollup_category_contributions helper, preserving descending amount/sort-order
ties, child display rules and distinct transaction counts. Transfers are
excluded; destination amounts and execution rates never enter reference FX.

Detail preserves explicit bounds or derives missing bounds from selected
transactions. Future transactions are included. Months are chronological and
gaps are complete zeros; no selected records still means series=[], as before.
CategorySpendingPoint minimally inherits FinancialDataStatus with nullable
amount. Missing FX nulls only affected points, total and average. Response
metric_status covers total_amount, average_per_month and series; known months
remain present when series status is incomplete. Ranking uses items=[] with
items/total_amount statuses incomplete instead of misleading partial shares.
Income and expense requests stay independent. MissingRate reason/date/max-age/
latest-date metadata is preserved and deduplicated without merging dates.

Reports keeps a small local success/failure cache keyed by source/target/date/
max-age and skips FX for exact zero. Extracting Dashboard/Cash Flow wrappers is
deferred to avoid changing accepted consumers in this stage. The only direct
backend callers are the routes. Frontend guards top-level incompleteness before
reading series/items; nullable point types and partial-series rendering remain
frontend work.

Search audit: Reports sums only converted points/category rows. Native split
amounts are safe same-transaction allocation inputs. There is no raw monetary
sum, today/period-end FX, transfer_amount, NULL-to-zero fallback or mixed-native
percentage in Reports/routes. category_rollup.py retains its old raw loader,
now unused by application callers; removal is deferred, and Reports calls only
the already-denominated rollup helper. No FX policy was added to that module.

test_reports_fx.py verifies dated conversion, future-rate isolation, category
shares/hierarchy, residual reconciliation, independent failure months, empty/
zero/filter behavior, native-data-preserving base changes, low ambient context,
cached successes/failures and request isolation. Provider/HTTP calls are
forbidden and FX statements must be SELECT only. No migration, frontend change,
persistent converted values or synchronization/backfill is introduced.
Budget, Goal, Recurring, Crypto, backup v2 and full Insights/Advice FX remain
separate work.

### Budget-native spending through dated FX

Budget.currency already exists as VARCHAR(3) NOT NULL with no model/server
default. Migration a72e9b4c106d snapshots AppSettings(id=1).currency, validated
and uppercased against its frozen catalog, or frozen USD when absent. No legacy
currency is rewritten by this stage. Create resolves omitted currency through
effective_default_currency and persists it. Explicit currency uses CurrencyCode.
PATCH still requires monthly_limit; optional explicit currency re-denominates
that nominal number without conversion. Omission preserves currency, explicit
null/unsupported currency fails validation. No locks or new migration added.

| Field | Currency / persistence | Completeness dependency |
| --- | --- | --- |
| monthly_limit (CRUD/status) | Persisted Budget.currency nominal | Always known |
| spent | Derived flow in Budget.currency | Every qualifying expense allocation |
| remaining | Derived monthly_limit - spent, Budget.currency | Complete spent |
| percent | Derived Decimal spent / limit * 100, float only at API boundary | Complete spent; existing zero-limit result is 0 |
| is_over_budget | Derived spent > limit boolean | Complete spent |

The budget remains one ongoing monthly limit per expense category, evaluated
for the requested calendar year/month. Boundaries are inclusive first/last
days, future transactions in that month remain included, and each month starts
with zero consumption. Parent budgets include their children; child budgets
still independently track the child's allocations. Empty months are known zero.

Budget status selects relevant EXPENSE transactions before resolution. Each
full Transaction.amount in Account.currency converts on Transaction.date
directly into the relevant Budget.currency via existing resolve_rate/convert.
All stored splits are then allocated in ID order, with the final split receiving
the same precision residual as Dashboard/Reports. Category filtering follows
allocation, so deleted/null shares are not redistributed. Income, transfers,
unrelated/uncategorized records and out-of-period records do not consume budgets
or require FX. No transfer_amount or execution-rate reference valuation.

BudgetStatus inherits FinancialResponseStatus: each row has its own currency,
complete/missing_rates/data_issues and metric_status. Spent, remaining, percent
and is_over_budget are null when FX is absent/stale/precision_unrepresentable;
monthly_limit stays known. BudgetStatusResponse inherits FinancialDataStatus,
merging rows without assigning a false single currency to the collection.
Complete rows survive other incomplete budgets. Resolver metadata retains
source/target/date/max-age/latest-date; identical errors are deduplicated, dates
and target currencies remain distinct.

One Budget-local cache spans all rows, keyed by source/target/date/max-age and
stores successes and MissingRate. Zero bypasses lookup. Settings supplies only
fx_max_age_days to valuation, never its target. Display-currency changes leave
nominal budget data and every progress value unchanged. Calculations run at
precision 96 with no monetary quantization. Shared-wrapper extraction is
deferred; existing Dashboard/Cash Flow/Reports wrappers are unchanged.

Direct-consumer audit: Insights counts over-budget booleans only when the whole
budget status is complete; otherwise its existing conservative alert contract
returns incomplete with no alerts. No partial count or unknown-as-false result.
Advice reads Budget.category_id only (non-monetary membership); its raw expense
sums and broader FX integration remain deferred behind its existing guard.
Budget production paths contain no raw expense/split SQL sums or comparison
against a limit before conversion. Native part amounts are same-transaction
allocation inputs; spent sums converted Budget.currency amounts; the Insights
sum counts booleans, not money. CRUD/backup limits remain persisted nominals.

test_budget_fx.py covers persistence, re-denomination, same/mixed currencies,
dated 2250 USD example, non-app VND targets through direct/inverse/bridge routes,
splits, independent budgets, missing metadata, max-age, period/type filters,
base-setting invariance and native row snapshots, cache targets/dates/failures,
zero/legacy zero limit, low ambient precision and direct Insights compatibility.
Provider/HTTP calls are forbidden during GET/list; FX statements must be SELECT.

Frontend is deliberately unchanged and NOT yet compatible with incomplete
budget rows: BudgetList calls percent.toFixed without a null guard and formats
values in the global currency. Per-budget currency labels and unknown-value UI
must be added during the frontend stage before this is user-facing. No Goal,
Recurring, Crypto, backup-v2 or full Insights/Advice FX work is included.

### Native Goal state (no reference FX)

Actual domain: Goal persists target_amount and currency; GoalContribution
persists signed amount, date and optional note. Both monetary columns are
Numeric(20,6). Contributions are manual deposits/withdrawals denominated in
their owning Goal.currency. There is no persisted current_amount, Account link,
Transaction link, source-currency input or automatic financial transaction.
The log is authoritative: current_amount is its PostgreSQL sum per Goal.id,
including future-dated contributions regardless of the goal deadline.

Goal.currency is already VARCHAR(3) NOT NULL with no database/model default.
Migration a72e9b4c106d backfills from AppSettings(id=1).currency, uppercased and
validated against a frozen catalog, falling back to frozen USD only if absent.
CRUD uses shared CurrencyCode/CurrencyUpdate validation. Omitted create currency
snapshots effective_default_currency once; later settings changes never alter
the goal or its journal. Explicit null target_amount in PATCH now returns 422
instead of reaching a NOT NULL database error; omission still preserves target.

| Field | Authority / classification |
| --- | --- |
| target_amount | Persisted nominal Goal.currency target |
| GoalContribution.amount | Persisted nominal signed delta in Goal.currency |
| current_amount | Same-goal sum of journal amounts, Goal.currency |
| remaining | Same-goal target - current, Goal.currency; may be negative |
| percent | Dimensionless current / target * 100; zero target yields 0 |
| is_reached | Same-goal current >= target |

Derived arithmetic now uses local precision 96 and ROUND_HALF_EVEN, with float
only for the existing percentage wire field and no cents quantization. Negative
current/legacy target, overfunding, target dates and completed semantics remain
unchanged. Empty logs are known zero; no completeness/MissingRate machinery is
needed or added.

Existing safe currency-update rules are retained: a different currency requires
an empty journal plus explicit replacement target_amount. Any journal row,
including offsetting deposits/withdrawals with net zero, gives 409
currency_change_requires_empty_history. With no journal but no replacement
target, return 409 currency_change_requires_target_replacement. Same-currency
PATCH is allowed. The API cannot re-enter an entire journal, so there is no
re-denomination path for goals with contributions and no automatic conversion.
No persistent lock flag: PATCH and contribution creation serialize on the same
Goal row with FOR UPDATE. Tests observe real PostgreSQL blocking in both orders.

Search audit: the only Goal service callers are the routes. Insights/Advice
contain no Goal consumers. Goal SQL sums are grouped by Goal.id/currency, not
across goals; coalesce means an actually empty journal, not unknown valuation.
No settings-currency interpretation, mixed-currency total, FX resolver/provider
or network exists in active Goal paths. Tests preserve simultaneous USD/VND/RUB
goals, settings-change row snapshots and contributions after settings changes;
provider/HTTP/resolver calls and all fx_rates SQL are forbidden in CRUD tests.

Backup remains unchanged: the existing transitional v1 optional currency field
preserves explicit values and restores omitted values using backup settings;
contributions restore under their goal. Versioned semantics remain Backup v2
work. Existing migration/backup tests remain part of regression verification.
Frontend still omits currency in Goal types/forms and formats using global
currency: later work must add selectors, row/contribution currency labels,
explicit re-denomination UX and 409 handling without global-currency fallback.
No frontend, migration, Account/Transaction progress derivation, Recurring,
Crypto, Backup v2 or full Insights/Advice FX integration is added here.

### Recurring native instructions and explicit revalidation

Schema inspection found this stage's requested columns already present:
amount_currency VARCHAR(3) NOT NULL, transfer_amount Numeric(20,6) NULL and
transfer_amount_currency VARCHAR(3) NULL were added by a72e9b4c106d, whose
down_revision is f83a2c91d607. Current head remains c4e8a91b720f (FX). No new,
duplicate or no-op migration is added and no historical migration is changed.
The existing revision validates source accounts/currency codes, adds nullable
fields, copies uppercase source currency, copies transfer destination currency
when present, fills destination amount only for same-currency transfers, then
makes source currency NOT NULL. Old cross-currency transfers had no destination
amount column, so their amount stays NULL. Missing destinations also remain
unresolved. This self-contained revision has frozen constants, no runtime/env
dependency, and is tested with absent settings. Its existing guarded downgrade
covers the original native-invariant changes, not only recurring columns;
upgrade/downgrade/re-upgrade and loss-prevention tests remain in force.

Model inventory is unchanged: id, account_id, category_id, transfer_account_id,
type, amount, amount_currency, transfer_amount, transfer_amount_currency,
description, merchant, notes, frequency, anchor_date, last_posted_date,
is_active, created_at, updated_at and account/category/destination relationships.
Source deletion cascades templates; destination/category deletion sets their
references NULL. Account deletion with actual transaction history stays blocked.

Only /recurring routes call CRUD/post services. There is no scheduler, job,
batch due posting or posting through Insights/Advice. List computes next_due
from anchor_date or advance(last_posted_date, frequency), is_due additionally
requires is_active. Manual Post now still dates one Transaction today and moves
last_posted_date to today; it does not backfill occurrences or enforce is_due.
Weekly/monthly/yearly advancement, end-of-month/leap-day clamping are unchanged.

Create uses sorted Account FOR UPDATE locks and refreshes currencies before
normalizing the instruction. Source snapshot always comes from the source;
transfer destination snapshot comes from the destination. Same-currency amount
normalizes exactly to amount, unequal explicit destination amounts fail. Cross
requires an explicit positive destination amount, never a reference rate.
Optional legacy currency inputs are assertions that must equal derived values,
not client authority. Invalid final instructions cannot enter through create
or monetary PATCH; explicit null required fields are rejected by the schema.

PATCH detects explicit monetary re-entry using model_fields_set for amount,
account_id, type, transfer_account_id, transfer_amount (even unchanged values).
It locks the union of discovered old and supplied new Account IDs in ascending
order, then locks/reloads the template. Non-monetary updates preserve snapshots
and unresolved amounts. Snapshot-only edits are rejected. Monetary cross PATCH
must explicitly re-enter destination amount; cross-to-same normalizes it.
Transfer-to-income/expense clears destination ID/amount/currency and rejects
explicit non-null destination fields. No stale snapshot is refreshed by editing
description, category or schedule. Source/destination snapshots refresh together
only after final monetary validation.

Posting uses the same account-first/template-second lock helper. Template
NOWAIT prevents an inverse template-first editor from deadlocking this path.
Changed account references yield recurring_accounts_changed_retry, busy or
concurrently changed instructions yield recurring_instruction_changed_retry.
Posting compares the discovered last_posted_date/updated_at version after lock:
two posts that discovered the same due instruction cannot both advance it.
This is concurrent-request protection, not an idempotency key for intentional
later Post now requests. Existing manual posting behavior is otherwise retained.

Under locks posting checks both currencies: mismatch gives 409
recurring_currency_mismatch. Missing destination, unresolved destination amount
or invalid legacy monetary state gives 409 recurring_currency_revalidation_required.
Failure leaves last_posted_date and Transactions untouched. Successful posting
uses TransactionCreate and shared normalize_transfer, copying both amounts.
No amount/rate conversion or execution-rate persistence is introduced.

Audit: source/destination amounts remain nominal instructions with snapshots;
same-currency normalization is exact Decimal; posting copies execution amounts;
dates/flags are schedule metadata. There are no sums, float money, FX resolver,
provider or fx_rates accesses in recurring paths. Actual Transaction history
alone controls Account currency edits; even a posted template is not history
after its Transactions are deleted. Read snapshots plus posting conflicts are
the backend status contract; no persistent or derived extra boolean is added.

Backup v1 already preserves explicit snapshots and derives omitted legacy ones
from restored accounts, with only same-currency amount inference. Its code and
format are unchanged; legacy unresolved templates remain readable/reconcilable.
Tests cover mismatch/re-entry, cross postings and native balances, deleted
accounts, lock ordering/old-new union, create/PATCH/post races, concurrent posts,
existing migration roundtrips, backup compatibility and zero FX/provider/HTTP.
Frontend remains unchanged: currency labels, destination amount input, explicit
monetary confirmation and conflict handling remain required. Crypto, Backup v2,
full Insights/Advice FX and frontend work are deferred.
