import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BudgetFormModal } from "@/components/budget/BudgetFormModal";
import { BudgetList } from "@/components/budget/BudgetList";
import { GoalFormModal } from "@/components/goals/GoalFormModal";
import { GoalList } from "@/components/goals/GoalList";
import { CryptoAddModal } from "@/components/crypto/CryptoAddModal";
import { CryptoTransactionModal } from "@/components/crypto/CryptoTransactionModal";
import { CryptoTransactionHistoryModal } from "@/components/crypto/CryptoTransactionHistoryModal";
import { CryptoHoldingsTable } from "@/components/crypto/CryptoHoldingsTable";
import { CryptoAllocationBody } from "@/components/crypto/CryptoAllocationBody";
import { CryptoNetworkAllocationBody } from "@/components/crypto/CryptoNetworkAllocationBody";
import { CryptoRiskAllocationBody } from "@/components/crypto/CryptoRiskAllocationBody";
import { CryptoHistoryChartBody } from "@/components/crypto/CryptoHistoryChartBody";
import { CryptoPage } from "@/pages/CryptoPage";
import { AssetsTable } from "@/components/networth/AssetsTable";
import { CurrencyCard } from "@/components/settings/CurrencyCard";
import { ApiError } from "@/api/client";
import { makeHolding } from "@/test/cryptoFixtures";
import { formatCurrency, formatCryptoAmount } from "@/lib/format";
import { setLanguage, setCurrency, t } from "@/lib/i18n";
import type { Asset, Budget, BudgetStatus, Goal, CryptoTransaction } from "@/types";
const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), charts: [] as Record<string, unknown>[][], connect: [] as unknown[] }));
vi.mock("@/api/client", async (original) => ({ ...await original<typeof import("@/api/client")>(), api: { get: mocks.get, post: mocks.post, patch: mocks.patch } }));
vi.mock("recharts", () => {
  const frame = ({ children }: { children?: ReactNode }) => <div>{children}</div>;
  const chart = ({ data, children }: { data: Record<string, unknown>[]; children?: ReactNode }) => { mocks.charts.push(data); return <div>{children}</div>; };
  return { ResponsiveContainer: frame, PieChart: frame, AreaChart: chart, Pie: chart, Cell: () => null, YAxis: () => null,
    Area: ({ connectNulls }: { connectNulls: boolean }) => { mocks.connect.push(connectNulls); return null; }, Tooltip: () => null };
});
const ok = { complete: true, missing_rates: [], data_issues: [] };
const missing = { complete: false, missing_rates: [{ base_currency: "RUB", quote_currency: "USD", requested_date: "2026-01-01", reason: "absent" as const, max_age_days: 7, latest_available_date: null }], data_issues: [] };
const noop = () => {};
const budget: Budget = { id: 1, category_id: 1, category_name: "Food", category_color: "#123456", category_icon: null, monthly_limit: "10000", currency: "RUB" };
const goal: Goal = { id: 1, name: "Trip", currency: "USD", target_amount: "1000", target_date: null, current_amount: "250", remaining: "750", percent: 25, is_reached: false };
const tx: CryptoTransaction = { id: 1, asset_id: 1, type: "buy", quantity: "0.5", price_per_unit: "100000.123456789123456789", quote_currency: "RUB", date: "2026-01-01", note: null };
const asset: Asset = { id: 1, name: "Native asset", asset_class: "other", currency: "RUB", current_value: "10000", monthly_cash_flow: "100", valuation_currency: "RUB", valuation_currency_confirmed: true, as_of_date: "2026-01-01", capital_role: "income", risk_level: "low", notes: null };
let box: HTMLDivElement, root: ReturnType<typeof createRoot>, client: QueryClient;
let settings: { currency: string };
let transactions: CryptoTransaction[];
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.resetAllMocks(); mocks.charts.length = 0; mocks.connect.length = 0;
  setLanguage("en"); setCurrency("USD"); settings = { currency: "USD" }; transactions = [tx];
  mocks.get.mockImplementation(async (path: string) => {
    if (path === "/settings") return settings;
    if (path === "/categories") return [{ id: 1, name: "Food", kind: "expense", color: "#123456", icon: null, parent_id: null }];
    if (path.startsWith("/crypto/portfolios")) return [];
    if (path.startsWith("/crypto/search")) return [{ coingecko_id: "bitcoin", symbol: "BTC", name: "Bitcoin", thumb_url: null }];
    if (path.endsWith("/transactions")) return transactions;
    if (path.startsWith("/crypto/holdings")) return { ...ok, currency: settings.currency, holdings: [makeHolding({ currency: settings.currency, value: settings.currency === "USD" ? "100" : "2500000" })], synced: false, last_synced_at: null, error_key: null };
    if (path.startsWith("/crypto/history")) return { ...ok, currency: settings.currency, range: "all", current: "100", change_amount: "0", change_percent: 0, series: [] };
    throw new Error(`Unexpected request: ${path}`);
  });
  mocks.post.mockResolvedValue({}); mocks.patch.mockImplementation(async (path, input) => { if (path === "/settings") { settings = { ...settings, ...input }; return settings; } return {}; });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  box = document.createElement("div"); document.body.append(box); root = createRoot(box);
});
afterEach(() => { act(() => root.unmount()); client.clear(); box.remove(); setLanguage("ru"); setCurrency("USD"); Reflect.deleteProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT"); });
async function settle(ms = 25) { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); }
async function render(node: ReactNode) { await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)); await settle(); await settle(); }
async function input(selector: string, value: string) {
  const el = box.querySelector(selector) as HTMLInputElement | HTMLSelectElement;
  expect(el).not.toBeNull();
  await act(async () => { Object.getOwnPropertyDescriptor(el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype, "value")!.set!.call(el, value); el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true })); });
}
async function submit() { await act(async () => { box.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); }); await settle(); }
async function baseVnd() { await act(async () => { settings = { currency: "VND" }; client.setQueryData(["settings"], settings); setCurrency("VND"); }); await settle(); }
it("new RUB Budget sends explicit currency and exact limit", async () => {
  await render(<BudgetFormModal open onClose={noop} excludeCategoryIds={[]} />);
  await input("#budget-category", "1"); await input("#planning-currency", "RUB"); await input("#budget-limit", "10000.123456"); await submit();
  expect(mocks.post).toHaveBeenCalledWith("/budgets", { category_id: 1, currency: "RUB", monthly_limit: "10000.123456" });
});
it("existing Budget keeps RUB after settings change", async () => {
  await render(<BudgetFormModal open onClose={noop} budget={budget} excludeCategoryIds={[]} />); await baseVnd(); await submit();
  expect(mocks.patch).toHaveBeenCalledWith("/budgets/1", { currency: "RUB", monthly_limit: "10000" });
});
it("budget uses backend remaining and never turns missing FX into zero", async () => {
  const row: BudgetStatus = { ...ok, ...budget, budget_id: 1, spent: "2000", remaining: "8000", percent: 20, is_over_budget: false };
  await render(<BudgetList items={[row]} onEdit={noop} onDelete={noop} />);
  expect(box.textContent).toContain(formatCurrency("8000", "RUB"));
  await render(<BudgetList items={[{ ...row, ...missing, spent: null, remaining: null, percent: null, is_over_budget: null }]} onEdit={noop} onDelete={noop} />);
  expect(box.textContent).toContain(t("financial.incomplete"));
  expect(box.textContent).not.toContain(formatCurrency("0", "RUB") + " /");
  expect(box.textContent).not.toContain(t("budget.remaining", { amount: formatCurrency("10000", "RUB") }));
});
it("new VND Goal sends explicit currency", async () => {
  await render(<GoalFormModal open onClose={noop} />); await input("#goal-name", "Trip"); await input("#goal-target", "10000000.123456"); await input("#planning-currency", "VND"); await submit();
  expect(mocks.post).toHaveBeenCalledWith("/goals", expect.objectContaining({ currency: "VND", target_amount: "10000000.123456" }));
});
it("Goal and native Asset survive base changes; backend progress and exact ROI", async () => {
  await render(<><GoalList items={[goal]} onEdit={noop} onDelete={noop} onContribute={noop} /><AssetsTable items={[asset]} onEdit={noop} onDelete={noop} /></>); await baseVnd();
  expect(box.textContent).toContain(formatCurrency("250", "USD")); expect(box.textContent).toContain("25%");
  expect(box.textContent).toContain(formatCurrency("10000", "RUB")); expect(box.textContent).toContain("12.0");
  await render(<AssetsTable items={[{ ...asset, valuation_currency_confirmed: false }]} onEdit={noop} onDelete={noop} />);
  expect(box.textContent).toContain(t("financial.unknown")); expect(box.textContent).not.toContain("12.0");
});
it("existing Goal edit preserves its stored currency", async () => {
  await render(<GoalFormModal open onClose={noop} goal={goal} />); await baseVnd(); await submit();
  expect(mocks.patch).toHaveBeenCalledWith("/goals/1", expect.objectContaining({ currency: "USD" }));
});
it("initial purchase remains RUB when Settings changes while form is open", async () => {
  await render(<CryptoAddModal open onClose={noop} />);
  await input("input", "bitcoin"); await settle(450);
  await act(async () => { Array.from(box.querySelectorAll("button")).find((b) => b.textContent?.includes("Bitcoin"))!.click(); });
  await input("#planning-currency", "RUB");
  const fields = box.querySelectorAll('input[inputmode="decimal"]');
  await input(`#${fields[0].id}`, "0.5"); await input(`#${fields[1].id}`, tx.price_per_unit); await baseVnd(); await submit();
  expect(mocks.post).toHaveBeenCalledWith("/crypto/holdings", expect.objectContaining({ quantity: "0.5", price_per_unit: tx.price_per_unit, quote_currency: "RUB" }));
  expect(mocks.patch).not.toHaveBeenCalled();
});
it("new existing-holding trade sends quote explicitly", async () => {
  await render(<CryptoTransactionModal open onClose={noop} holding={makeHolding()} />);
  await input("#planning-currency", "RUB"); await input("#tx-quantity", "0.5"); await input("#tx-price", tx.price_per_unit); await submit();
  expect(mocks.post).toHaveBeenCalledWith(expect.stringMatching(/transactions$/), expect.objectContaining({ quote_currency: "RUB", price_per_unit: tx.price_per_unit }));
});
it("legacy metadata-only PATCH never reconciles; explicit selector reconciles nominally", async () => {
  const legacy = { ...tx, quote_currency: null };
  const holding = makeHolding();
  await render(<CryptoTransactionModal open onClose={noop} holding={holding} transaction={legacy} />); await baseVnd();
  expect((box.querySelector("#planning-currency") as HTMLSelectElement).value).toBe("");
  await input("#tx-note", "memo"); await submit(); expect(mocks.patch).toHaveBeenLastCalledWith("/crypto/transactions/1", { note: "memo" });
  await input("#planning-currency", "RUB"); await submit();
  expect(mocks.patch).toHaveBeenLastCalledWith("/crypto/transactions/1", expect.objectContaining({ quote_currency: "RUB", price_per_unit: tx.price_per_unit }));
});
it("historical quote remains RUB; legacy quote is explicitly unknown", async () => {
  transactions = [tx, { ...tx, id: 2, quote_currency: null }];
  await render(<CryptoTransactionHistoryModal open onClose={noop} holding={makeHolding({ asset_id: 1 })} hidden={false} onEdit={noop} />); await baseVnd();
  expect(box.textContent).toContain(formatCryptoAmount(tx.price_per_unit, "RUB")); expect(box.textContent).toContain(t("planning.unknownQuote"));
});
it.each([400, 409, 422])("rejection %s localizes and invalidates without closing/relabeling", async (status) => {
  mocks.patch.mockRejectedValue(new ApiError(status, "currency_change_requires_empty_history")); const close = vi.fn(); const invalidate = vi.spyOn(client, "invalidateQueries");
  await render(<GoalFormModal open onClose={close} goal={goal} />); await input("#planning-currency", "RUB"); await submit();
  expect(close).not.toHaveBeenCalled(); expect(box.textContent).toContain(t("goal.form.saveError")); expect(invalidate).toHaveBeenCalledWith({ queryKey: ["goals"] }); expect(goal.currency).toBe("USD");
});
it("cache requires both date and currency", async () => {
  const holding = makeHolding({ current_price: "777", last_price_currency: "RUB" });
  const props = { hidden: false, onTrade: noop, onViewHistory: noop, onDelete: noop, onRiskChange: noop, onNetworkChange: noop };
  await render(<CryptoHoldingsTable {...props} items={[holding]} />); expect(box.textContent).toContain(formatCryptoAmount("777", "RUB"));
  await render(<CryptoHoldingsTable {...props} items={[{ ...holding, last_price_as_of_date: null }]} />); expect(box.textContent).toContain(t("planning.priceUnknown")); expect(box.textContent).not.toContain(formatCryptoAmount("777", "RUB"));
});
it.each([CryptoAllocationBody, CryptoNetworkAllocationBody, CryptoRiskAllocationBody])("allocation uses response currency and rejects partial totals", async (Component) => {
  await render(<Component currency="VND" holdings={[makeHolding({ currency: "VND", value: "2000000" })]} isLoading={false} hidden={false} />);
  expect(box.textContent).toContain(Component === CryptoRiskAllocationBody ? formatCurrency("2000000", "VND") : formatCryptoAmount("2000000", "VND"));
  await render(<Component currency="VND" holdings={[makeHolding({ currency: "VND" }), makeHolding({ currency: "VND", value: null, ...missing })]} isLoading={false} hidden={false} />);
  expect(box.textContent).toContain(t("financial.unknown")); expect(box.textContent).not.toContain("100%");
});
it("history preserves null gaps and tooltip originals", async () => {
  await render(<CryptoHistoryChartBody hidden={false} isLoading={false} history={{ ...missing, currency: "VND", range: "all", current: null, change_amount: null, change_percent: null, series: [{ ...ok, date: "2026-01-01", value: "123456789.123456789" }, { ...missing, date: "2026-02-01", value: null }] }} />);
  expect(mocks.charts.at(-1)).toEqual([{ date: "2026-01-01", value: 123456789.123456789, original: "123456789.123456789" }, { date: "2026-02-01", value: null, original: null }]); expect(mocks.connect.at(-1)).toBe(false);
});
it("settings mutation refetches aggregate Crypto into VND", async () => {
  await render(<><CurrencyCard /><CryptoPage /></>);
  expect(box.textContent).toContain(formatCurrency("100", "USD"));
  await input("select", "VND"); await settle(); await settle();
  expect(box.textContent).toContain(formatCurrency("2500000", "VND"));
});
it.each(["ru", "en"] as const)("localizes unresolved provenance in %s", async (language) => {
  setLanguage(language); await render(<CryptoTransactionModal open onClose={noop} holding={makeHolding()} transaction={{ ...tx, quote_currency: null }} />);
  expect(box.textContent).toContain(t("planning.reconcileQuote")); expect(box.textContent).toContain(t("planning.unknownQuote"));
});
it("incomplete Crypto page never displays the known subset as its total", async () => {
  const previous = mocks.get.getMockImplementation()!;
  mocks.get.mockImplementation(async (path: string) => path.startsWith("/crypto/holdings")
    ? { ...missing, currency: "USD", holdings: [makeHolding({ value: "12345" }), makeHolding({ ...missing, value: null, cost_basis: null, profit_loss: null })], synced: false, last_synced_at: null, error_key: null }
    : previous(path));
  await render(<CryptoPage />);
  const heading = Array.from(box.querySelectorAll("p")).find((el) => el.querySelector('button[aria-label]'))!;
  expect(heading.textContent).toContain(t("financial.unknown"));
  expect(heading.textContent).not.toContain(formatCurrency("12345", "USD"));
  expect(box.textContent).toContain(t("financial.rateUnavailable"));
});
it("known execution edit sends stored RUB despite VND settings", async () => {
  await render(<CryptoTransactionModal open onClose={noop} holding={makeHolding()} transaction={tx} />); await baseVnd();
  await input("#tx-quantity", "0.6"); await submit();
  expect(mocks.patch).toHaveBeenCalledWith("/crypto/transactions/1", expect.objectContaining({ quote_currency: "RUB", quantity: "0.6", price_per_unit: tx.price_per_unit }));
});
it("invalid precision never reaches crypto API or rounds the form", async () => {
  await render(<CryptoTransactionModal open onClose={noop} holding={makeHolding()} />);
  await input("#tx-quantity", "1"); await input("#tx-price", "0.0000000000000000001"); await submit();
  expect(mocks.post).not.toHaveBeenCalled(); expect(box.textContent).toContain(t("planning.invalidCrypto"));
  expect((box.querySelector("#tx-price") as HTMLInputElement).value).toBe("0.0000000000000000001");
});
it("zero budget spending is distinct from missing FX", async () => {
  await render(<BudgetList items={[{ ...ok, ...budget, budget_id: 1, spent: "0", remaining: "10000", percent: 0, is_over_budget: false }]} onEdit={noop} onDelete={noop} />);
  expect(box.textContent).toContain(formatCurrency("0", "RUB")); expect(box.textContent).toContain(t("budget.remaining", { amount: formatCurrency("10000", "RUB") }));
  expect(box.textContent).not.toContain(t("financial.unknown"));
});
