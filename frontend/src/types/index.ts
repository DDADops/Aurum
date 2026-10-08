// Shared contracts for endpoints as they adopt explicit financial completeness.
// Endpoint types adopt these contracts together with their consumers.
export interface MissingRate {
  base_currency: string;
  quote_currency: string;
  requested_date: string;
  reason: "absent" | "stale" | "precision_unrepresentable";
  max_age_days: number;
  latest_available_date: string | null;
}

export type KnownFinancialDataIssueCode =
  | "transfer_amount_unresolved"
  | "transfer_destination_missing"
  | "crypto_quote_currency_unknown"
  | "valuation_currency_unconfirmed"
  | "valuation_missing"
  | "price_missing";

export interface FinancialDataIssue {
  // Unknown server codes use the same safe, generic reconciliation message.
  code: KnownFinancialDataIssueCode | (string & {});
}

export interface FinancialDataStatus {
  complete: boolean;
  missing_rates: MissingRate[];
  data_issues: FinancialDataIssue[];
}

export type MetricStatus<Metric extends string = string> = Partial<Record<Metric, FinancialDataStatus>>;

export interface FinancialResponseStatus<Metric extends string = string> extends FinancialDataStatus {
  currency: string;
  metric_status?: MetricStatus<Metric>;
}

export type FinancialValue = string | number | null | undefined;

export type AccountType = "checking" | "debit_card" | "savings" | "credit_card" | "cash" | "investment" | "other";
export type CategoryKind = "income" | "expense";
export type TransactionType = "income" | "expense" | "transfer";
export type RecurringFrequency = "weekly" | "monthly" | "yearly";

export interface Account {
  id: number;
  name: string;
  type: AccountType;
  currency: string;
  color: string | null;
  is_archived: boolean;
}

// The Accounts management page's shape — /api/accounts' own endpoints add a
// live `balance` (summed from transactions) that a nested Transaction.account
// never carries. Keep the two separate rather than making `balance` optional
// on `Account`, so a stray `tx.account.balance` read is a compile error, not
// a silent `undefined` at runtime.
export interface AccountWithBalance extends Account, FinancialDataStatus {
  balance: string | null;
  has_monetary_history: boolean;
}

export interface AccountInput {
  currency: string;
  color?: string | null;
  name: string;
  type: AccountType;
}

export interface Category {
  id: number;
  name: string;
  kind: CategoryKind;
  icon: string | null;
  color: string;
  sort_order: number;
  is_default: boolean;
  // Subcategory parent, one level deep only (a category whose own parent_id
  // is set can't itself have children — enforced backend-side).
  parent_id: number | null;
}

export interface CategoryInput {
  name: string;
  kind: CategoryKind;
  icon?: string | null;
  color: string;
  sort_order?: number;
  parent_id?: number | null;
}

// kind is fixed at creation on the backend (CategoryUpdate has no kind field).
export interface CategoryUpdateInput {
  name?: string;
  icon?: string | null;
  color?: string;
  sort_order?: number;
  parent_id?: number | null;
}

export interface Tag {
  id: number;
  name: string;
}

// One category's slice of a transaction whose amount is divided across
// several categories (one receipt, several kinds of goods) — see
// TransactionInput.splits. category is null when the split's category was
// since deleted; reading must still work even though creating/editing a
// split always requires a live category.
export interface TransactionSplit {
  id: number;
  category_id: number | null;
  category: Category | null;
  amount: string;
  note: string | null;
}

export interface TransactionSplitInput {
  category_id: number;
  amount: string;
  note?: string | null;
}

export interface Transaction extends FinancialDataStatus {
  transfer_amount: string | null;
  effective_rate: string | null;
  transfer_account: Account | null;
  id: number;
  account_id: number;
  category_id: number | null;
  transfer_account_id: number | null;
  type: TransactionType;
  amount: string;
  description: string;
  merchant: string | null;
  notes: string | null;
  date: string;
  account: Account;
  category: Category | null;
  tags: Tag[];
  splits: TransactionSplit[];
}

export interface TransactionPage {
  items: Transaction[];
  total: number;
  page: number;
  page_size: number;
}

export interface TransactionInput {
  transfer_amount?: string | null;
  account_id: number;
  category_id: number | null;
  transfer_account_id: number | null;
  type: TransactionType;
  amount: string;
  description: string;
  merchant?: string | null;
  notes?: string | null;
  date: string;
  // Omitted -> tags untouched on update; sent (even as []) -> replaces the
  // full tag set. Always sent on create (defaults to []).
  tag_ids?: number[];
  // Omitted -> a normal single-category transaction (unchanged). 2+ entries
  // -> the amount is divided across categories instead, and category_id
  // above must then be null. On update, omitted leaves existing splits
  // untouched; sent (even as []) replaces the full split set.
  splits?: TransactionSplitInput[] | null;
}

export interface RecurringTransaction {
  amount_currency: string;
  transfer_amount: string | null;
  transfer_amount_currency: string | null;
  id: number;
  account_id: number;
  account_name: string;
  category_id: number | null;
  category_name: string | null;
  category_color: string | null;
  category_icon: string | null;
  transfer_account_id: number | null;
  transfer_account_name: string | null;
  type: TransactionType;
  amount: string;
  description: string;
  merchant: string | null;
  notes: string | null;
  frequency: RecurringFrequency;
  anchor_date: string;
  last_posted_date: string | null;
  is_active: boolean;
  next_due_date: string;
  is_due: boolean;
  days_until_due: number;
}

export interface RecurringTransactionInput {
  amount_currency?: string;
  transfer_amount?: string | null;
  transfer_amount_currency?: string | null;
  notes?: string | null;
  account_id: number;
  category_id: number | null;
  transfer_account_id: number | null;
  type: TransactionType;
  amount: string;
  description: string;
  merchant?: string | null;
  frequency: RecurringFrequency;
  anchor_date: string;
}

export type RecurringTransactionUpdateInput = Partial<RecurringTransactionInput> & { is_active?: boolean };

// One subcategory's (or the parent's own direct, un-subcategorized) share
// of a CategoryBreakdownItem's total. Populated only when more than one
// distinct category fed the slice — a single-source category (the common
// case) leaves this off the parent item entirely.
export interface CategoryBreakdownChildItem {
  category_id: number;
  name: string;
  color: string;
  icon: string | null;
  amount: string;
}

export interface CategoryBreakdownItem {
  category_id: number | null;
  name: string;
  color: string;
  icon: string | null;
  amount: string;
  percent: number;
  children: CategoryBreakdownChildItem[];
}

export interface DashboardSummary extends FinancialResponseStatus {
  year: number;
  month: number;
  real_income: string | null;
  spent: string | null;
  net: string | null;
  transferred_out: string | null;
  spending_by_category: CategoryBreakdownItem[];
}

export type AssetClass = "investments" | "crypto" | "real_estate" | "vehicles" | "precious_metals" | "other";
export type NetWorthRange = "30d" | "90d" | "1y" | "5y" | "all";
export type CapitalRole = "income" | "neutral" | "drain";
export type RiskLevel = "low" | "medium" | "high";

export interface Asset {
  has_monetary_history: boolean;
  valuation_currency: string | null;
  valuation_currency_confirmed: boolean;
  id: number;
  name: string;
  asset_class: AssetClass;
  currency: string;
  notes: string | null;
  capital_role: CapitalRole;
  monthly_cash_flow: string | null;
  risk_level: RiskLevel;
  current_value: string;
  as_of_date: string;
}

export interface AssetInput {
  name: string;
  asset_class: AssetClass;
  currency: string;
  notes?: string | null;
  capital_role?: CapitalRole;
  monthly_cash_flow?: string | null;
  risk_level?: RiskLevel;
  value: string;
  as_of_date: string;
}

export interface AssetUpdateInput {
  currency?: string;
  name?: string;
  asset_class?: AssetClass;
  notes?: string | null;
  capital_role?: CapitalRole;
  monthly_cash_flow?: string | null;
  risk_level?: RiskLevel;
}

export interface AssetValuationInput {
  expected_currency: string;
  value: string;
  as_of_date: string;
}

export interface NetWorthPoint extends FinancialDataStatus {
  date: string;
  value: string | null;
}

export interface NetWorthBreakdownItem {
  key: string;
  name: string;
  color: string;
  icon: string;
  amount: string;
  percent: number;
}

export interface CapitalRoleSummary {
  role: CapitalRole;
  label: string;
  color: string;
  total_value: string;
  monthly_cash_flow: string;
  count: number;
}

export interface RiskLevelItem {
  key: string;
  name: string;
  amount: string;
  percent: number;
}

export interface RiskLevelSummary {
  risk_level: RiskLevel;
  label: string;
  color: string;
  total_value: string;
  percent: number;
  items: RiskLevelItem[];
}

export interface NetWorthSummary extends FinancialResponseStatus {
  range: NetWorthRange;
  current: string | null;
  change_amount: string | null;
  change_percent: number | null;
  series: NetWorthPoint[];
  breakdown: NetWorthBreakdownItem[];
  capital_roles: CapitalRoleSummary[];
  risk_levels: RiskLevelSummary[];
}

export interface CategorySpendingPoint extends FinancialDataStatus {
  year: number;
  month: number;
  amount: string | null;
}

export interface CategorySpendingReport extends FinancialResponseStatus {
  category_id: number;
  category_name: string;
  category_color: string;
  category_icon: string | null;
  start_date: string | null;
  end_date: string | null;
  total_amount: string | null;
  transaction_count: number;
  average_per_month: string | null;
  series: CategorySpendingPoint[];
}

export interface CashFlowPoint extends FinancialDataStatus {
  metric_status?: MetricStatus<"income" | "expense" | "net">;
  year: number;
  month: number;
  income: string | null;
  expense: string | null;
  net: string | null;
}

export interface CashFlowResponse extends FinancialResponseStatus {
  start_date: string | null;
  end_date: string | null;
  points: CashFlowPoint[];
  total_income: string | null;
  total_expense: string | null;
  total_net: string | null;
}

export interface CategoryRankingChildItem {
  category_id: number;
  name: string;
  color: string;
  icon: string | null;
  amount: string;
}

export interface CategoryRankingItem {
  category_id: number;
  name: string;
  color: string;
  icon: string | null;
  amount: string;
  percent: number;
  transaction_count: number;
  children: CategoryRankingChildItem[];
}

export interface CategoryRankingReport extends FinancialResponseStatus {
  start_date: string | null;
  end_date: string | null;
  total_amount: string | null;
  items: CategoryRankingItem[];
}

export interface Goal {
  currency: string;
  id: number;
  name: string;
  target_amount: string;
  target_date: string | null;
  current_amount: string;
  remaining: string;
  percent: number;
  is_reached: boolean;
}

export interface GoalInput {
  currency: string;
  name: string;
  target_amount: string;
  target_date: string | null;
}

export interface GoalContributionInput {
  amount: string;
  date: string;
  note?: string | null;
}

export interface Budget {
  currency: string;
  id: number;
  category_id: number;
  category_name: string;
  category_color: string;
  category_icon: string | null;
  monthly_limit: string;
}

export interface BudgetInput {
  currency: string;
  category_id: number;
  monthly_limit: string;
}

export interface BudgetStatus extends FinancialResponseStatus {
  currency: string;
  budget_id: number;
  category_id: number;
  category_name: string;
  category_color: string;
  category_icon: string | null;
  monthly_limit: string;
  spent: string | null;
  remaining: string | null;
  percent: number | null;
  is_over_budget: boolean | null;
}

export interface BudgetStatusResponse extends FinancialDataStatus {
  year: number;
  month: number;
  items: BudgetStatus[];
}

export interface AdviceItem {
  key: string;
  tone: "positive" | "neutral" | "warning";
  params: Record<string, string | number>;
}

export interface AdviceResponse extends FinancialResponseStatus {
  items: AdviceItem[];
}

export interface FinancialAlert {
  key: string;
  severity: string;
  params: Record<string, number>;
}

export type CryptoTransactionType = "buy" | "sell";

export interface CryptoPortfolio {
  id: number;
  name: string;
  color: string | null;
  is_archived: boolean;
}

export interface CryptoPortfolioInput {
  name: string;
  is_archived?: boolean;
}

export interface CryptoHolding extends FinancialResponseStatus {
  currency: string;
  last_price_currency: string | null;
  last_price_as_of_date: string | null;
  cost_basis_currency: string | null;
  asset_id: number;
  portfolio_id: number;
  coingecko_id: string;
  symbol: string;
  name: string;
  thumb_url: string | null;
  // Lives on the underlying Asset — edited via PATCH /assets/{asset_id},
  // same endpoint every other Asset already uses.
  risk_level: RiskLevel;
  // Free-form chain name (e.g. "Ethereum", "Tron") — not validated against
  // a fixed list, since the same coin can carry different bridge/
  // counterparty risk per chain and risk_level alone can't capture that.
  // Edited via PATCH /crypto/holdings/{asset_id}.
  network: string | null;
  // Both derived from the buy/sell log (see CryptoTransaction) — never
  // edited directly.
  quantity: string;
  avg_buy_price: string | null;
  // Cached from the last successful CoinGecko sync — null means "added but
  // never priced yet" (CoinGecko was unreachable right at creation),
  // distinct from a real zero.
  current_price: string | null;
  price_change_1h: string | null;
  price_change_24h: string | null;
  price_change_7d: string | null;
  price_change_30d: string | null;
  // Stands in for "all time" on the Best/Worst Performer stat — CoinGecko's
  // free tier caps historical lookback at 365 days regardless.
  price_change_1y: string | null;
  value: string | null;
  cost_basis: string | null;
  profit_loss: string | null;
  profit_loss_percent: number | null;
}

export interface CryptoHoldingCreateInput {
  quote_currency: string;
  // Omit to file the coin under the default portfolio (auto-created if
  // none exists yet — see services/crypto_service.py's
  // get_or_create_default_portfolio).
  portfolio_id?: number | null;
  coingecko_id: string;
  symbol: string;
  name: string;
  thumb_url?: string | null;
  // A holding always starts with its first buy.
  quantity: string;
  price_per_unit: string;
  date: string;
  note?: string | null;
  // Omit to default to "high" server-side — crypto's own textbook risk
  // tier — but overridable per coin (a large-cap isn't a memecoin).
  risk_level?: RiskLevel;
  // Free text, e.g. "Ethereum" or "Tron" — see CryptoHolding.network.
  network?: string | null;
}

export interface CryptoHoldingUpdateInput {
  network?: string | null;
}

export interface CryptoTransaction {
  quote_currency: string | null;
  id: number;
  asset_id: number;
  type: CryptoTransactionType;
  quantity: string;
  price_per_unit: string;
  date: string;
  note: string | null;
}

export interface CryptoTransactionInput {
  quote_currency: string;
  type: CryptoTransactionType;
  quantity: string;
  price_per_unit: string;
  date: string;
  note?: string | null;
}

export interface CryptoSyncResult extends FinancialResponseStatus {
  currency: string;
  synced: boolean;
  last_synced_at: string | null;
  error_key: "unreachable" | null;
  holdings: CryptoHolding[];
}

export interface CryptoSearchResult {
  coingecko_id: string;
  symbol: string;
  name: string;
  thumb_url: string | null;
}

// Real 90-day % price change per coin, fetched on demand only while the
// 90d range is selected — see services/crypto_service.py's
// get_90d_performance for why this can't ride along with the regular sync.
export interface CryptoPerformancePoint {
  asset_id: number;
  price_change_percent: number | null;
}

export interface CryptoPerformanceResponse {
  items: CryptoPerformancePoint[];
}

// No "24h" — the price history is only as dense as the sync cadence (see
// services/crypto_service.py's AUTO_REFRESH_INTERVAL), so a 24h chart would
// be one or two points, not a smooth intraday line.
export type CryptoRange = "7d" | "30d" | "90d" | "all";

export interface CryptoHistoryPoint extends FinancialDataStatus {
  date: string;
  value: string | null;
}

export interface CryptoHistoryResponse extends FinancialResponseStatus {
  currency: string;
  range: CryptoRange;
  current: string | null;
  change_amount: string | null;
  change_percent: number | null;
  series: CryptoHistoryPoint[];
}

export interface AppSettings {
  currency: string;
  negative_cash_flow_threshold_months: number;
  net_worth_decline_threshold_months: number;
  risky_allocation_threshold_percent: number;
  idle_cash_threshold_amount: string;
  idle_cash_threshold_days: number;
  /** Read-only: the running backend release. Comes back here rather than
   * from /api/health, which is served without auth. */
  app_version: string;
}


export interface AlertsResponse extends FinancialResponseStatus {
  alerts: FinancialAlert[];
}
