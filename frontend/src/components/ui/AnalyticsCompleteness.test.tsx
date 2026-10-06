import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "@/pages/DashboardPage";
import { NetWorthPage } from "@/pages/NetWorthPage";
import { ReportsPage } from "@/pages/ReportsPage";
import { AdvicePage } from "@/pages/AdvicePage";
import { CashFlowChart } from "@/components/cashflow/CashFlowChart";
import { CategorySpendingChart } from "@/components/reports/CategorySpendingChart";
import { AlertBanner } from "@/components/insights/AlertBanner";
import { AccountList } from "@/components/accounts/AccountList";
import { CurrencyCard } from "@/components/settings/CurrencyCard";
import { FinancialDataStatus } from "./FinancialDataStatus";
import { setCurrency, setLanguage, t } from "@/lib/i18n";
import { formatCurrency } from "@/lib/format";
import { transferAccount } from "@/test/transactionFixtures";
import type { CashFlowResponse, DashboardSummary, NetWorthSummary, CategorySpendingReport, AdviceResponse, AlertsResponse, AppSettings, CategoryRankingReport } from "@/types";

const mocks = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), chartData: [] as Record<string, unknown>[][], connections: [] as unknown[] }));
vi.mock("@/api/client", () => ({ api: { get: mocks.get, patch: mocks.patch } }));
vi.mock("recharts", () => {
  const frame = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  const chart = ({ children, data }: { children?: ReactNode; data: Record<string, unknown>[] }) => {
    mocks.chartData.push(data); return <div data-testid="chart">{children}</div>;
  };
  return { ResponsiveContainer: frame, BarChart: chart, AreaChart: chart, PieChart: frame,
    Pie: chart, Area: ({ connectNulls }: { connectNulls?: boolean }) => { mocks.connections.push(connectNulls); return null; },
    Bar: () => null, Tooltip: () => null, Legend: () => null, XAxis: () => null, YAxis: () => null, Cell: () => null };
});
const ok = { complete: true, missing_rates: [], data_issues: [] };
const missing = { complete: false, missing_rates: [{ base_currency: "RUB", quote_currency: "USD", requested_date: "2026-01-01", reason: "absent" as const, max_age_days: 7, latest_available_date: null }], data_issues: [] };
const noop = () => {};
function dashboard(): DashboardSummary {
  return { ...ok, currency: "USD", year: 2026, month: 1, real_income: "1000", spent: "100", net: "900", transferred_out: "0",
    spending_by_category: [{ category_id: 1, name: "Food", color: "#123456", icon: null, amount: "100", percent: 100,
      children: [{ category_id: 2, name: "Fruit", color: "#123456", icon: null, amount: "100" }] }] };
}
function netWorth(): NetWorthSummary {
  return { ...ok, currency: "VND", range: "5y", current: "3000000", change_amount: "1000000", change_percent: 50,
    series: [{ ...ok, date: "2026-01-01", value: "2000000" }, { ...ok, date: "2026-02-01", value: "3000000" }],
    breakdown: [{ key: "cash", name: "Cash", color: "#123456", icon: "wallet", amount: "3000000", percent: 100 }],
    capital_roles: [{ role: "income", label: "Income", color: "#123456", total_value: "0", monthly_cash_flow: "0", count: 0 }], risk_levels: [] };
}
function flow(): CashFlowResponse {
  return { ...ok, currency: "USD", start_date: null, end_date: null, total_income: "0", total_expense: "0", total_net: "0",
    points: [{ ...ok, metric_status: { income: ok, expense: ok, net: ok }, year: 2026, month: 1, income: "0", expense: "0", net: "0" }] };
}
function report(): CategorySpendingReport {
  return { ...ok, currency: "VND", category_id: 1, category_name: "Food", category_color: "#123456", category_icon: null,
    start_date: null, end_date: null, total_amount: "3000000", average_per_month: "1500000", transaction_count: 2,
    series: [{ ...ok, year: 2026, month: 1, amount: "1000000" }, { ...ok, year: 2026, month: 2, amount: "2000000" }] };
}

describe("analytics response contracts", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  let client: QueryClient;
  let dash: DashboardSummary;
  let worth: NetWorthSummary;
  let spending: CategorySpendingReport;
  let ranking: CategoryRankingReport;
  let advice: AdviceResponse;
  let alerts: AlertsResponse;
  let settings: AppSettings;
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.resetAllMocks(); mocks.chartData.length = 0; mocks.connections.length = 0;
    setLanguage("en"); setCurrency("RUB");
    dash = dashboard(); worth = netWorth(); spending = report();
    ranking = { ...ok, currency: "VND", start_date: null, end_date: null, total_amount: "0", items: [] };
    advice = { ...ok, currency: "USD", items: [] }; alerts = { ...ok, currency: "USD", alerts: [] };
    settings = { currency: "USD", app_version: "test", idle_cash_threshold_amount: "1000", idle_cash_threshold_days: 60,
      negative_cash_flow_threshold_months: 2, net_worth_decline_threshold_months: 2, risky_allocation_threshold_percent: 20 };
    mocks.get.mockImplementation(async (path: string) => {
      if (path.startsWith("/dashboard/summary")) return dash;
      if (path.startsWith("/net-worth/summary")) return worth;
      if (path.startsWith("/reports/category-spending")) return spending;
      if (path.startsWith("/reports/category-ranking")) return ranking;
      if (path === "/advice") return advice;
      if (path === "/insights/alerts") return alerts;
      if (path === "/settings") return settings;
      if (path === "/transactions/years") return [2026];
      if (path.startsWith("/transactions?")) return { items: [], total: 0, page: 1, page_size: 20 };
      if (path === "/categories") return [{ id: 1, name: "Food", kind: "expense", parent_id: null, color: "#123456", icon: null }];
      if (path.startsWith("/accounts") || path === "/assets" || path === "/tags") return [];
      throw new Error(`Unexpected API/FX request: ${path}`);
    });
    mocks.patch.mockImplementation(async (_path: string, input: Partial<AppSettings>) => { settings = { ...settings, ...input }; return settings; });
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount()); client.clear(); container.remove(); setLanguage("ru"); setCurrency("USD");
    Reflect.deleteProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT");
  });
  async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); }); }
  async function render(node: ReactNode) {
    await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>)); await settle(); await settle();
  }
  function chart() { return mocks.chartData.at(-1)!; }

  it("Dashboard uses response USD instead of global RUB and exact savings percentage", async () => {
    await render(<DashboardPage />);
    expect(container.textContent).toContain(formatCurrency("1000", "USD"));
    expect(container.textContent).toContain("+90%");
    expect(chart()[0].amount).toBe("100"); // donut uses backend percent, not Number(amount)
  });
  it("Dashboard preserves independent complete income and hides incomplete partial expenses", async () => {
    dash = { ...dash, ...missing, spent: null, net: null, spending_by_category: [],
      metric_status: { real_income: ok, spent: missing, net: missing, spending_by_category: missing } };
    await render(<DashboardPage />);
    expect(container.textContent).toContain(formatCurrency("1000", "USD"));
    expect(container.textContent).toContain(t("financial.rateUnavailable"));
    expect(container.textContent).not.toContain("$0"); expect(mocks.chartData).toHaveLength(0);
  });
  it("USD → VND refetch updates the open category detail and leaves native RUB untouched", async () => {
    const native = Object.freeze({ ...transferAccount(1, "RUB"), balance: "100000" });
    await render(<><DashboardPage /><CurrencyCard /><AccountList items={[native]} onEdit={noop} onDelete={noop} onToggleArchived={noop} /></>);
    act(() => container.querySelector<HTMLButtonElement>(`button[aria-label="${t("common.expand")}"]`)!.click()); await settle();
    expect(container.textContent).toContain("$100");
    dash = { ...dashboard(), currency: "VND", real_income: "25000000", spent: "2500000", net: "22500000",
      spending_by_category: dashboard().spending_by_category.map((item) => ({ ...item, amount: "2500000", children: item.children.map((child) => ({ ...child, amount: "2500000" })) })) };
    act(() => {
      const select = container.querySelector<HTMLSelectElement>('select[aria-label="Base / display currency"]')!;
      select.value = "VND"; select.dispatchEvent(new Event("change", { bubbles: true }));
    }); await settle(); await settle();
    expect(container.textContent).toContain(formatCurrency("25000000", "VND"));
    expect(container.textContent).toContain(formatCurrency("2500000", "VND"));
    expect(container.textContent).not.toContain(formatCurrency("100", "VND"));
    expect(container.textContent).toContain(formatCurrency("100000", "RUB"));
    expect(native.currency).toBe("RUB"); expect(native.balance).toBe("100000");
  });
  it("Net Worth uses authoritative total and backend allocation percentages", async () => {
    await render(<NetWorthPage />);
    expect(container.textContent).toContain(formatCurrency("3000000", "VND"));
    expect(container.textContent).toContain("100%");
    expect(chart()[1]).toMatchObject({ value: 3000000, original: "3000000" });
  });
  it("Net Worth incomplete current hides partial allocations but keeps known history and gaps", async () => {
    worth = { ...worth, ...missing, current: null, change_amount: null, change_percent: null,
      breakdown: [], capital_roles: [], series: [worth.series[0], { ...missing, date: "2026-02-01", value: null }] };
    await render(<NetWorthPage />);
    expect(container.textContent).toContain(t("financial.unknown")); expect(container.textContent).not.toContain("100%");
    expect(chart().map((point) => point.value)).toEqual([2000000, null]);
    expect(mocks.connections).toContain(false);
  });
  it("Net Worth independently hides capital roles and unavailable comparison, retaining current", async () => {
    worth = { ...worth, change_amount: null, change_percent: null, capital_roles: [],
      metric_status: { current: ok, breakdown: ok, risk_levels: ok, capital_roles: missing, change_amount: missing },
      series: [{ ...missing, date: "2026-01-01", value: null }, worth.series[1]] };
    await render(<NetWorthPage />);
    expect(container.textContent).toContain(formatCurrency("3000000", "VND"));
    expect(container.textContent).toContain(t("financial.rateUnavailable"));
    expect(container.textContent).not.toContain(t("netWorth.capitalByType"));
    expect(chart()[0].value).toBeNull();
  });
  it("Cash Flow complete zero is a true zero coordinate and value", async () => {
    await render(<CashFlowChart cashFlow={flow()} isLoading={false} />);
    expect(chart()[0]).toMatchObject({ income: 0, expense: 0, net: 0 }); expect(container.textContent).toContain("$0");
  });
  it("Cash Flow preserves independent known period income and nullable expense/net gaps", async () => {
    const response: CashFlowResponse = { ...flow(), ...missing, total_income: "50", total_expense: null, total_net: null,
      metric_status: { total_income: ok, total_expense: missing, total_net: missing },
      points: [{ ...missing, year: 2026, month: 1, income: "50", expense: null, net: null,
        metric_status: { income: ok, expense: missing, net: missing } }] };
    await render(<CashFlowChart cashFlow={response} isLoading={false} />);
    expect(container.textContent).toContain("$50"); expect(container.textContent).not.toContain("$0");
    expect(chart()[0]).toMatchObject({ income: 50, expense: null, net: null, original: { income: "50", expense: null, net: null } });
  });
  it("Reports complete values use response VND and preserve Decimal tooltip originals", async () => {
    await render(<CategorySpendingChart report={spending} isLoading={false} />);
    expect(container.textContent).toContain(formatCurrency("3000000", "VND"));
    expect(chart()[0]).toMatchObject({ amount: 1000000, original: "1000000" });
  });
  it("Reports incomplete totals/ranking stay unavailable while known months remain visible", async () => {
    spending = { ...spending, ...missing, total_amount: null, average_per_month: null,
      series: [spending.series[0], { ...missing, year: 2026, month: 2, amount: null }] };
    ranking = { ...ranking, ...missing, total_amount: null };
    await render(<ReportsPage />);
    expect(container.textContent).toContain(t("financial.rateUnavailable"));
    expect(chart().map((point) => point.amount)).toEqual([1000000, null]);
    expect(container.textContent).not.toContain("100%");
  });
  it("missing denomination never uses Settings or USD for chart data", async () => {
    const response = { ...flow(), currency: undefined } as unknown as CashFlowResponse;
    await render(<CashFlowChart cashFlow={response} isLoading={false} />);
    expect(chart()[0].income).toBeNull(); expect(container.textContent).not.toContain("$0");
    expect(container.textContent).toContain(t("financial.unknown"));
  });
  it.each(["en", "ru"] as const)("%s distinguishes missing/stale/precision FX and other data issues", async (language) => {
    setLanguage(language);
    for (const [reason, key] of [["absent", "financial.rateUnavailable"], ["stale", "financial.rateStale"], ["precision_unrepresentable", "financial.ratePrecision"]] as const) {
      await render(<FinancialDataStatus status={{ ...missing, missing_rates: [{ ...missing.missing_rates[0], reason }] }} />);
      expect(container.textContent).toContain(t(key));
    }
    await render(<FinancialDataStatus status={{ ...ok, complete: false, data_issues: [{ code: "transfer_amount_unresolved" }] }} />);
    expect(container.textContent).toContain(t("financial.reconciliation"));
  });
  it("Insights keeps independent complete alerts visible; params are counts, not money", async () => {
    alerts = { ...alerts, ...missing, metric_status: { idle_cash: ok, risky_allocation_exceeded: missing },
      alerts: [{ key: "idle_cash", severity: "warning", params: { count: 1, days: 60 } }] };
    await render(<AlertBanner />);
    expect(container.textContent).toContain(t("financial.rateUnavailable"));
    expect(container.textContent).toContain(t("insights.idleCashOne", { count: 1, days: 60 }));
  });
  it("Advice shows independent complete monetary advice in its explicit response denomination", async () => {
    advice = { ...advice, ...missing, currency: "VND", metric_status: { unbudgeted_top_category: ok, rising_category: missing },
      items: [{ key: "unbudgeted_top_category", tone: "neutral", params: { category: "Food", amount: "3000000", currency: "VND" } }] };
    await render(<AdvicePage />);
    expect(container.textContent).toContain(formatCurrency("3000000", "VND")); expect(container.textContent).toContain(t("financial.rateUnavailable"));
  });
  it("Advice defensively treats an unexpected null as unknown instead of zero", async () => {
    advice.items = [{ key: "unbudgeted_top_category", tone: "neutral", params: { category: "Food", amount: null, currency: "USD" } } as unknown as AdviceResponse["items"][number]];
    await render(<AdvicePage />);
    expect(container.textContent).toContain(t("financial.unknown")); expect(container.textContent).not.toContain("$0");
  });
});
