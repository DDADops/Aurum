import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AccountList } from "./AccountList";
import { AccountFormModal } from "./AccountFormModal";
import { AccountsPage } from "@/pages/AccountsPage";
import { CurrencyCard } from "@/components/settings/CurrencyCard";
import { useDeleteTransaction } from "@/hooks/useTransactions";
import { usePostRecurring } from "@/hooks/useRecurring";
import * as accountsApi from "@/api/accounts";
import * as settingsApi from "@/api/settings";
import * as transactionsApi from "@/api/transactions";
import * as recurringApi from "@/api/recurring";
import { ApiError } from "@/api/client";
import { formatCurrency } from "@/lib/format";
import { setCurrency, setLanguage, t } from "@/lib/i18n";
import type { AccountWithBalance, AppSettings } from "@/types";

vi.mock("@/api/accounts");
vi.mock("@/api/settings");
vi.mock("@/api/transactions");
vi.mock("@/api/recurring");

const settings: AppSettings = {
  currency: "USD", negative_cash_flow_threshold_months: 3, net_worth_decline_threshold_months: 3,
  risky_allocation_threshold_percent: 70, idle_cash_threshold_amount: "10000",
  idle_cash_threshold_days: 90, app_version: "test",
};
function account(overrides: Partial<AccountWithBalance> = {}): AccountWithBalance {
  return { id: 1, name: "Salary", type: "checking", currency: "RUB", color: "#123456", is_archived: false,
    balance: "100000", complete: true, missing_rates: [], data_issues: [], has_monetary_history: true, ...overrides };
}
const noop = () => {};
function HistoryMutations() {
  const deletion = useDeleteTransaction();
  const posting = usePostRecurring();
  return <><button id="delete-history" onClick={() => deletion.mutate(1)}>delete</button>
    <button id="post-template" onClick={() => posting.mutate(1)}>post</button></>;
}

describe("native account currency", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  let client: QueryClient;
  const cleanupObservers: (() => void)[] = [];
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.resetAllMocks();
    setLanguage("en"); setCurrency("USD");
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
    client.setQueryData(["settings"], settings);
    vi.mocked(settingsApi.fetchSettings).mockResolvedValue(settings);
    vi.mocked(settingsApi.updateSettings).mockImplementation(async (input) => ({ ...settings, ...input }));
    vi.mocked(accountsApi.fetchAccounts).mockResolvedValue([account()]);
    vi.mocked(accountsApi.createAccount).mockImplementation(async (input) => account({ ...input, balance: "0", has_monetary_history: false }));
    vi.mocked(accountsApi.updateAccount).mockImplementation(async (_id, input) => account({ ...input }));
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    cleanupObservers.splice(0).forEach((unsubscribe) => unsubscribe());
    client.clear(); container.remove(); setLanguage("ru"); setCurrency("USD");
    Reflect.deleteProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT");
  });
  async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); }); }
  async function render(node: ReactNode) {
    await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>));
    await settle();
  }
  async function select(selector: string, value: string) {
    const el = container.querySelector<HTMLSelectElement>(selector)!;
    act(() => { el.value = value; el.dispatchEvent(new Event("change", { bubbles: true })); });
    await settle();
  }
  function enterName(value: string) {
    const el = container.querySelector<HTMLInputElement>("#account-name")!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
  }
  async function submit() {
    await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await settle();
  }
  function list(items: AccountWithBalance[]) {
    return <AccountList items={items} onEdit={noop} onDelete={noop} onToggleArchived={noop} />;
  }

  it.each(["USD", "RUB", "VND"])("renders %s in its native denomination", async (currency) => {
    await render(list([account({ currency })]));
    expect(container.textContent).toContain(currency);
    expect(container.textContent).toContain(formatCurrency("100000", currency));
  });
  it.each(["en", "ru"] as const)("renders mixed accounts and localized labels in %s", async (language) => {
    setLanguage(language);
    await render(<>{list([account(), account({ id: 2, currency: "USD" }), account({ id: 3, currency: "VND" })])}
      <AccountFormModal open onClose={noop} account={account()} /><CurrencyCard /></>);
    for (const currency of ["USD", "RUB", "VND"]) expect(container.textContent).toContain(formatCurrency("100000", currency));
    expect(container.querySelector('label[for="account-currency"]')?.textContent).toBe(language === "ru" ? "Валюта счёта" : "Account currency");
    expect(container.textContent).toContain(language === "ru" ? "Базовая валюта / валюта отображения" : "Base / display currency");
    expect(container.querySelector("#account-currency-history")?.textContent).toBe(t("account.currencyHistory"));
  });
  it.each([null, "0"])("does not present incomplete %s as zero", async (balance) => {
    await render(list([account({ balance, complete: false, data_issues: [{ code: "transfer_amount_unresolved" }] })]));
    expect(container.textContent).toContain(t("financial.reconciliation"));
    expect(container.textContent).not.toContain(formatCurrency("0", "RUB"));
  });
  it("shows unknown for NULL even if the status is unexpectedly complete", async () => {
    await render(list([account({ balance: null })]));
    expect(container.textContent).toContain(t("financial.unknown"));
    expect(container.textContent).not.toContain(formatCurrency("0", "RUB"));
  });
  it("keeps high precision Decimal data unchanged through rendering and editing", async () => {
    const row = Object.freeze(account({ balance: "9007199254740993.123456789012345678" }));
    await render(<>{list([row])}<AccountFormModal open onClose={noop} account={row} /></>);
    await submit();
    expect(row.balance).toBe("9007199254740993.123456789012345678");
    expect(accountsApi.updateAccount).toHaveBeenCalledWith(1, { name: "Salary", type: "checking", currency: "RUB" });
  });
  it.each(["USD", "VND"])("creates with settings default %s sent explicitly", async (currency) => {
    client.setQueryData(["settings"], { ...settings, currency });
    await render(<AccountFormModal open onClose={noop} />);
    expect(container.querySelector<HTMLSelectElement>("#account-currency")!.value).toBe(currency);
    enterName("Cash");
    await submit();
    expect(accountsApi.createAccount).toHaveBeenCalledWith({ name: "Cash", type: "checking", currency });
  });
  it("allows overriding the default and does not erase it on settings refetch", async () => {
    await render(<AccountFormModal open onClose={noop} />);
    await select("#account-currency", "VND");
    act(() => client.setQueryData(["settings"], { ...settings, currency: "RUB" })); await settle();
    expect(container.querySelector<HTMLSelectElement>("#account-currency")!.value).toBe("VND");
    enterName("Cash");
    await submit();
    expect(accountsApi.createAccount).toHaveBeenCalledWith({ name: "Cash", type: "checking", currency: "VND" });
  });
  it.each(["0", "100000", null])("blocks currency changes with history, balance %s", async (balance) => {
    await render(<AccountFormModal open onClose={noop} account={account({ balance })} />);
    expect(container.querySelector<HTMLSelectElement>("#account-currency")!.disabled).toBe(true);
    expect(container.textContent).toContain(t("account.currencyHistory"));
  });
  it("allows an empty account (including unused recurring instructions) to change currency", async () => {
    client.setQueryData(["recurring"], [{ account_id: 1, last_posted_date: null }]);
    await render(<AccountFormModal open onClose={noop} account={account({ balance: "0", has_monetary_history: false })} />);
    expect(container.querySelector<HTMLSelectElement>("#account-currency")!.disabled).toBe(false);
    await select("#account-currency", "VND"); await submit();
    expect(accountsApi.updateAccount).toHaveBeenCalledWith(1, { name: "Salary", type: "checking", currency: "VND" });
  });
  it("handles a concurrent first posting rejection without optimistic relabeling", async () => {
    vi.mocked(accountsApi.fetchAccounts).mockResolvedValue([account({ balance: "0", has_monetary_history: false })]);
    await render(<AccountsPage />);
    act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Edit"]')!.click()); await settle();
    await select("#account-currency", "VND");
    vi.mocked(accountsApi.updateAccount).mockRejectedValue(new ApiError(409, "currency_change_requires_empty_history"));
    vi.mocked(accountsApi.fetchAccounts).mockResolvedValue([account()]);
    await submit();
    expect(container.querySelector<HTMLSelectElement>("#account-currency")!.disabled).toBe(true);
    expect(container.querySelector<HTMLSelectElement>("#account-currency")!.value).toBe("RUB");
    expect(container.textContent).toContain(t("account.currencyHistory"));
    expect(container.querySelector("li")?.textContent).toContain(formatCurrency("100000", "RUB"));
  });
  it("refetches eligibility after history deletion and incoming recurring posting", async () => {
    await render(<><AccountsPage /><HistoryMutations /></>);
    act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Edit"]')!.click()); await settle();
    expect(container.querySelector<HTMLSelectElement>("#account-currency")!.disabled).toBe(true);
    vi.mocked(accountsApi.fetchAccounts).mockResolvedValue([account({ balance: "0", has_monetary_history: false })]);
    act(() => container.querySelector<HTMLButtonElement>("#delete-history")!.click()); await settle(); await settle();
    expect(container.querySelector<HTMLSelectElement>("#account-currency")!.disabled).toBe(false);
    vi.mocked(accountsApi.fetchAccounts).mockResolvedValue([account({ balance: null, complete: false, data_issues: [{ code: "transfer_amount_unresolved" }] })]);
    act(() => container.querySelector<HTMLButtonElement>("#post-template")!.click()); await settle(); await settle();
    expect(container.querySelector<HTMLSelectElement>("#account-currency")!.disabled).toBe(true);
    expect(container.querySelector("li")?.textContent).toContain(t("financial.reconciliation"));
    expect(transactionsApi.deleteTransaction).toHaveBeenCalledWith(1);
    expect(recurringApi.postRecurring).toHaveBeenCalledWith(1);
  });
  it("base USD → VND refetches aggregates and preserves native RUB cache and UI", async () => {
    const row = Object.freeze(account());
    client.setQueryData(["accounts", false], [row]);
    const nativeKeys = ["accounts", "budgets", "budget-status", "goals", "crypto-transactions"];
    for (const key of nativeKeys.slice(1)) client.setQueryData([key], { unchanged: true });
    const aggregateKeys = ["dashboard-summary", "net-worth-summary", "cash-flow", "category-spending-report",
      "category-ranking", "financial-alerts", "advice", "crypto-holdings", "crypto-history"];
    const fetchers = aggregateKeys.map((key) => {
      const fetcher = vi.fn(async () => ({ currency: client.getQueryData<AppSettings>(["settings"])!.currency }));
      const observer = new QueryObserver(client, { queryKey: [key, "filters"], queryFn: fetcher });
      cleanupObservers.push(observer.subscribe(noop)); return fetcher;
    });
    await render(<><AccountsPage /><CurrencyCard /></>);
    const initialText = container.querySelector("li")!.textContent;
    await select('select[aria-label="Base / display currency"]', "VND"); await settle();
    expect(settingsApi.updateSettings).toHaveBeenCalledWith({ currency: "VND" });
    expect(container.querySelector("li")!.textContent).toBe(initialText);
    expect(initialText).toContain(formatCurrency("100000", "RUB"));
    expect(client.getQueryData<AccountWithBalance[]>(["accounts", false])![0]).toEqual(row);
    aggregateKeys.forEach((key, index) => {
      expect(fetchers[index]).toHaveBeenCalledTimes(2);
      expect(client.getQueryData([key, "filters"])).toEqual({ currency: "VND" });
    });
    for (const key of nativeKeys) expect(client.getQueryState(key === "accounts" ? [key, false] : [key])?.isInvalidated).toBe(false);
    expect(accountsApi.fetchAccounts).not.toHaveBeenCalled();
  });
});
