# Backup v2

`GET /backup/export` emits `aurum_backup_version: 2`. `POST /backup/import`
accepts explicit integer version 1 or 2 in the same JSON body; success remains
`{"status":"ok"}`. Unsupported/malformed versions fail schema validation (422),
as do malformed types and precision. Relational/domain validation failures use
400. No version inference from fields, migration, frontend or provider dependency.

Top-level keys: `aurum_backup_version`, `exported_at`, `app_version`, `accounts`,
`categories`, `tags`, `transactions`, `transaction_splits`, `assets`,
`asset_valuations`, `crypto_portfolios`, `crypto_holdings`, `crypto_transactions`,
`budgets`, `goals`, `goal_contributions`, `recurring_transactions`, `app_settings`,
`fx_rates`, `crypto_sync_state`. All keys and all row fields are explicit in v2,
including NULL fields. Unknown v2 keys and omitted fields are rejected. Settings
is a singleton object; crypto sync state is a singleton object or NULL; table
collections are arrays. Export orders rows by primary key and tag IDs numerically.

## Authoritative field matrix

All listed fields are persisted authoritative data (including persisted caches).
Names/types match models; Decimal values are JSON strings, not float numbers.
Money uses Numeric(20,6), valuation/crypto/FX Numeric(38,18); cached percentage
changes use Numeric(10,4). Parsing validates finite values and allowed precision
without rounding. Validation uses local Decimal precision 96. Enum values use
existing API strings, dates ISO dates, and FX/sync timestamps timezone-aware ISO.

| Dataset | Exact v2 fields | v1 normalization |
|---|---|---|
| Accounts | id, name, type, currency, color, is_archived | Preserve, validate currencies. No balances or eligibility flags. |
| Categories | id, name, kind, icon, color, sort_order, is_default, parent_id | Preserve; absent historical parent_id is NULL. |
| Tags | id, name | Preserve; absent historical collection is empty. |
| Transactions | id, account_id, category_id, transfer_account_id, type, amount, transfer_amount, description, merchant, notes, date, tag_ids | Preserve nominal amount and any explicitly stored transfer_amount (including NULL). If omitted, same-currency transfer gets amount; different/missing destination gets NULL. tag_ids represent persisted transaction_tags relationships. |
| Transaction splits | id, transaction_id, category_id, amount, note | Preserve, including NULL category after deletion. |
| Assets | id, name, asset_class, currency, notes, capital_role, monthly_cash_flow, risk_level | Preserve nominal values; historical omitted role/risk defaults remain neutral/medium, monthly_cash_flow NULL. |
| Asset valuations | id, asset_id, value, as_of_date, currency, currency_confirmed | ALL v1 valuations become currency=NULL, currency_confirmed=false. No Asset.currency inference. |
| Crypto portfolios | id, name, color, is_archived | Preserve. Historical NULL/omitted holding portfolio uses the existing Main Portfolio compatibility fallback; deterministic max supplied portfolio ID + 1, entirely in memory. Non-NULL dangling portfolio IDs fail. |
| Crypto holdings | asset_id, portfolio_id, coingecko_id, symbol, name, thumb_url, network, last_price, last_price_currency, last_price_as_of_date, price_change_1h, price_change_24h, price_change_7d, price_change_30d, price_change_1y | Preserve numeric cache and metadata; quote/date become NULL. Absent network/older percentage fields default NULL. |
| Crypto transactions | id, asset_id, type, quantity, price_per_unit, quote_currency, date, note | Preserve numbers; quote_currency becomes NULL, never settings/asset/cache currency. |
| Budgets | id, category_id, currency, monthly_limit | Preserve explicit transitional currency; otherwise normalized backup settings currency. Never convert limits. |
| Goals | id, name, currency, target_amount, target_date | Preserve explicit transitional currency; otherwise normalized backup settings currency. Never convert targets. |
| Goal contributions | id, goal_id, amount, date, note | Preserve nominal journal, including withdrawals; denomination is restored Goal.currency. |
| Recurring | id, account_id, category_id, transfer_account_id, type, amount, amount_currency, transfer_amount, transfer_amount_currency, description, merchant, notes, frequency, anchor_date, last_posted_date, is_active | Source snapshot from restored Account.currency; transfer destination snapshot from restored destination, otherwise NULL. Missing destination amount is source amount only for same-currency transfers; otherwise NULL. Preserve explicitly stored destination amount. V2 never re-snapshots, even when Accounts differ. |
| FX rates | id, base_currency, quote_currency, rate, as_of_date, source, fetched_at | Empty; no provider request, inverse/bridge synthesis or reuse of current DB rates. V2 copies stored rows only. |
| Crypto sync state | id, last_synced_at | NULL when normalizing v1, which did not back up this operational marker; v2 preserves its actual persisted state. |

Every actual AppSettings persisted field is covered:

| Field | v2 | Historical v1 omission |
|---|---|---|
| id | Preserve singleton 1 | 1 |
| currency | Preserve validated code | Frozen USD, only if absent; invalid explicit currency is rejected |
| fx_max_age_days | Preserve nonnegative integer | 7 |
| negative_cash_flow_threshold_months | Preserve | 2 |
| net_worth_decline_threshold_months | Preserve | 2 |
| risky_allocation_threshold_percent | Preserve | 20 |
| idle_cash_threshold_amount | Preserve nominal Money | 1000 |
| idle_cash_threshold_days | Preserve | 60 |

`idle_cash_threshold_currency` does not exist and is not added. The stored pair
`currency` + `idle_cash_threshold_amount` defines the threshold denomination.
Normalization never consults current settings, environment or runtime defaults.

The established backup exclusion of DB-managed `created_at`/`updated_at` remains:
these volatile ORM timestamps are regenerated by database defaults. `exported_at`
is new on each export; `app_version` describes the exporting application. No
other persisted model columns are omitted (enforced by a coverage test).
Balances, effective transfer rates, aggregates, crypto quantity/value/cost/P&L,
risk totals, statuses, reconciliation/eligibility flags and request caches are
not backed up.

## Validation and legacy unknowns

V1 normalization operates on a deep copy before any database access/deletion.
Missing required source Accounts and non-NULL dangling references fail; no
current-DB lookup, guessed currency, dropped rows or rewritten FK IDs. Nullable
NULL destination references remain valid and distinct from dangling IDs.
The pre-portfolios fallback is an existing historical-format exception, not a
repair path for arbitrary broken foreign keys.

V2 preserves unresolved transfer_amount, recurring snapshots, unknown crypto
quote/cache provenance and valuation provenance exactly. Known valuation currency
with confirmed=false is supported. Confirmed=true requires currency. Cache
provenance must be a complete price/currency/date tuple or an unknown legacy price
with both currency/date NULL (all NULL is also valid). No refresh/reconciliation.

Before locking/replacement: validate version/structure, scalar types, currency,
Decimal bounds/signs, enums, dates/timezones, stored string/integer bounds, required
columns, PK uniqueness, all FKs, tag association uniqueness, category hierarchy,
transfer/split rules, same-denomination amounts where known, per-budget category
uniqueness, valuation (asset_id,date) uniqueness and FX (base,quote,date)
uniqueness. FX must be finite positive, with distinct currencies. No automatic
winner for duplicate rows. Required amounts are not replaced by zero.

## Atomicity, locks and order

PostgreSQL restore locks all 18 managed tables in alphabetic name order using
ACCESS EXCLUSIVE before any DELETE. Export acquires SHARE on the same set for a
consistent snapshot and performs no writes. Locks stay until transaction end;
no network calls occur. SQLite skips PostgreSQL-specific table/sequence SQL;
normal transaction semantics remain (the production checks run on PostgreSQL).

Delete order: transaction_tags, fx_rates, recurring_transactions,
goal_contributions, budgets, crypto_transactions, crypto_holdings,
asset_valuations, transaction_splits, transactions, goals, crypto_portfolios,
assets, tags, categories, accounts, crypto_sync_state, app_settings.

Insert order: accounts, categories (parents first), tags, assets,
crypto_portfolios, goals, transactions, transaction_splits, asset_valuations,
crypto_holdings, crypto_transactions, budgets, goal_contributions,
recurring_transactions, fx_rates, transaction_tags, app_settings,
crypto_sync_state. Core inserts preserve explicit IDs and NULLs without calling
financial writers. Database timestamps retain their existing default behavior.

All operations commit together or roll back together. Sequences use transactional
ALTER SEQUENCE RESTART at max restored ID + 1 (1 for empty tables), including
settings/sync singletons where a sequence exists. Unlike setval, sequence changes
roll back if the restore fails. Identity-map state is expired after success.

## API/frontend and verification

Routes, authentication, ordinary JSON transport and deployment limits are
unchanged. No file paths or unsafe deserialization were added. The frontend
downloads a blob and uploads JSON without inspecting version/fields, so no
frontend adjustment is needed for v2. The existing confirmation UI remains.

Tests compare export/restore/export semantically and persisted columns directly;
only exported_at and the established ORM timestamps are volatile. Tests cover
pre-delete validation, v1 defaults/FKs/provenance, unknown and known v2 tuples,
transactional rollback including sequences, subsequent IDs, PostgreSQL concurrent
writer blocking, no provider HTTP, read-only export, low Decimal precision, and
post-restore native balances/Dashboard/Cash Flow/Reports/Budget/Goal/Crypto/Net Worth.
