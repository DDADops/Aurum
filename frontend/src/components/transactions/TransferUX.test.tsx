import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TransactionFormModal } from "./TransactionFormModal";
import { TransactionsTable } from "./TransactionsTable";
import * as transactionsApi from "@/api/transactions";
import * as accountsApi from "@/api/accounts";
import * as categoriesApi from "@/api/categories";
import * as tagsApi from "@/api/tags";
import { useTransactions } from "@/hooks/useTransactions";
import { ApiError } from "@/api/client";
import { formatCurrency } from "@/lib/format";
import { setCurrency, setLanguage, t } from "@/lib/i18n";
import { transferAccount, transferAccounts, transferTransaction, unresolvedTransfer } from "@/test/transactionFixtures";
import type { Transaction } from "@/types";

vi.mock("@/api/transactions");
vi.mock("@/api/accounts");
vi.mock("@/api/categories");
vi.mock("@/api/tags");
const noop = () => {};
function LiveTransactions() {
  const { data } = useTransactions({});
  return <TransactionsTable items={data?.items ?? []} onEdit={noop} onDelete={noop} />;
}
const category = { id: 5, name: "Food", kind: "expense" as const, color: "#123456", icon: null, parent_id: null, sort_order: 0, is_default: false };

describe("Transfer UX", () => {
  let client: QueryClient;
  let root: ReturnType<typeof createRoot>;
  let container: HTMLDivElement;
  const subscriptions: (() => void)[] = [];
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    vi.resetAllMocks(); setLanguage("en"); setCurrency("EUR");
    vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Unexpected network / FX call"); }));
    vi.mocked(accountsApi.fetchAccounts).mockResolvedValue(transferAccounts);
    vi.mocked(categoriesApi.fetchCategories).mockResolvedValue([category]);
    vi.mocked(tagsApi.fetchTags).mockResolvedValue([]);
    vi.mocked(transactionsApi.createTransaction).mockResolvedValue(transferTransaction());
    vi.mocked(transactionsApi.updateTransaction).mockResolvedValue(transferTransaction());
    client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
    container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  });
  afterEach(() => {
    expect(fetch).not.toHaveBeenCalled(); // All permitted APIs are mocked; no FX/provider request is allowed.
    act(() => root.unmount()); subscriptions.splice(0).forEach((unsubscribe) => unsubscribe());
    client.clear(); container.remove(); vi.unstubAllGlobals(); setLanguage("ru"); setCurrency("USD");
    Reflect.deleteProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT");
  });
  async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)); }); }
  async function render(node: ReactNode) {
    await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)); await settle();
  }
  async function form(transaction?: Transaction) { await render(<TransactionFormModal open onClose={noop} transaction={transaction} />); }
  async function change(selector: string, value: string) {
    const el = container.querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
    expect(el, selector).not.toBeNull();
    act(() => {
      const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(el, value);
      el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
    }); await settle();
  }
  async function submit() {
    await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); }); await settle();
  }
  function value(id: string) { return container.querySelector<HTMLInputElement>(id)?.value; }
  function patch() { return vi.mocked(transactionsApi.updateTransaction).mock.calls.at(-1)?.[1]; }
  async function newTransfer(source = "1", destination = "2", amount = "10000", received = "3000000") {
    await form(); await change("#type", "transfer"); await change("#account", source);
    await change("#transfer_account", destination); await change("#amount", amount); await change("#description", "Transfer");
    if (container.querySelector("#transfer-amount")) await change("#transfer-amount", received);
  }

  it.each([["income", "1", "RUB"], ["expense", "2", "VND"]])("ordinary %s in %s uses one amount", async (type, account, currency) => {
    await form(); await change("#type", type); await change("#account", account); await change("#amount", "100.123456");
    await change("#description", "Ordinary"); await submit();
    expect(container.querySelector('label[for="amount"]')?.textContent).toContain(currency);
    expect(container.querySelector("#transfer-amount")).toBeNull();
    const input = vi.mocked(transactionsApi.createTransaction).mock.calls[0][0];
    expect(input.amount).toBe("100.123456"); expect(input).not.toHaveProperty("transfer_amount");
    expect(input.transfer_account_id).toBeNull();
  });
  it("same USD pair sends the exact source string and follows later edits", async () => {
    await newTransfer("3", "4", "100.123456");
    expect(container.querySelector("#transfer-amount")).toBeNull();
    await change("#amount", "99999999999999.999999"); await submit();
    expect(transactionsApi.createTransaction).toHaveBeenCalledWith(expect.objectContaining({ amount: "99999999999999.999999", transfer_amount: "99999999999999.999999" }));
    expect(container.textContent).not.toContain(t("transactions.form.transactionRate"));
  });
  it.each([["1", "2", "10000.123456", "3000000.123456", "RUB", "VND"], ["2", "3", "3000000", "120", "VND", "USD"]])("cross %s → %s keeps independent exact strings", async (source, destination, amount, received, debit, credit) => {
    await newTransfer(source, destination, amount, received); await submit();
    expect(transactionsApi.createTransaction).toHaveBeenCalledWith(expect.objectContaining({ amount, transfer_amount: received, category_id: null }));
    expect(container.querySelector('label[for="amount"]')?.textContent).toContain(debit);
    expect(container.querySelector('label[for="transfer-amount"]')?.textContent).toContain(credit);
    expect(container.querySelector("#category")).toBeNull();
    expect(container.textContent).toContain(t("transactions.form.transactionRate"));
  });
  it("same → cross clears destination; cross → same synchronizes exactly", async () => {
    await newTransfer("3", "4", "100.123456"); await change("#transfer_account", "2");
    expect(value("#transfer-amount")).toBe(""); await submit(); expect(transactionsApi.createTransaction).not.toHaveBeenCalled();
    await change("#transfer-amount", "1000"); await change("#transfer_account", "4"); await submit();
    expect(transactionsApi.createTransaction).toHaveBeenCalledWith(expect.objectContaining({ transfer_amount: "100.123456" }));
  });
  it.each([["#account", "3"], ["#transfer_account", "3"]])("changing %s clears a previous cross-pair credit", async (field, id) => {
    await newTransfer(); await change(field, id); expect(value("#transfer-amount")).toBe("");
    await submit(); expect(transactionsApi.createTransaction).not.toHaveBeenCalled();
  });
  it("changing only source amount keeps cross-pair credit", async () => {
    await newTransfer(); await change("#amount", "20000"); expect(value("#transfer-amount")).toBe("3000000");
    expect(container.textContent).toContain("1 RUB ≈ 150 VND"); await submit();
    expect(transactionsApi.createTransaction).toHaveBeenCalledWith(expect.objectContaining({ amount: "20000", transfer_amount: "3000000" }));
  });
  it("resolved edit preserves stored amounts and prefers server-derived rate", async () => {
    const tx = transferTransaction({ effective_rate: "300.000000000000000000" });
    await form(tx); expect(value("#amount")).toBe(tx.amount); expect(value("#transfer-amount")).toBe(tx.transfer_amount);
    expect(container.textContent).toContain(tx.effective_rate); await change("#description", "ATM cash"); await submit();
    expect(patch()).toEqual({ description: "ATM cash" });
  });
  it.each([["#account", "3"], ["#transfer_account", "3"]])("edit pair change %s needs fresh credit, even if equal to previous credit", async (field, id) => {
    await form(transferTransaction()); await change(field, id); expect(value("#transfer-amount")).toBe("");
    await change("#transfer-amount", "3000000.000000"); await submit();
    expect(patch()).toEqual({ [field === "#account" ? "account_id" : "transfer_account_id"]: 3, transfer_amount: "3000000.000000" });
  });
  it.each(["description", "merchant", "notes"])("unresolved %s-only PATCH preserves unresolved money", async (field) => {
    const tx = unresolvedTransfer(); vi.mocked(transactionsApi.updateTransaction).mockResolvedValue({ ...tx, [field]: "ATM cash" });
    await form(tx); expect(value("#transfer-amount")).toBe(""); await change(`#${field}`, "ATM cash"); await submit();
    expect(patch()).toEqual({ [field]: "ATM cash" }); expect(tx.transfer_amount).toBeNull();
    expect(container.textContent).toContain(t("transactions.needsReconciliation"));
  });
  it("unresolved tag-only edit omits every financial field", async () => {
    await form({ ...unresolvedTransfer(), tags: [{ id: 9, name: "Trip" }] });
    const remove = [...container.querySelectorAll<HTMLButtonElement>("button")].find((button) => button.getAttribute("aria-label") === t("transactions.form.removeTag", { name: "Trip" }))!;
    act(() => remove.click()); await submit(); expect(patch()).toEqual({ tag_ids: [] });
  });
  it("no-op unresolved save neither PATCHes nor invents a credit", async () => {
    await form(unresolvedTransfer()); await submit(); expect(transactionsApi.updateTransaction).not.toHaveBeenCalled();
    expect(value("#transfer-amount")).toBe("");
  });
  it("changed date requires reconciliation and submits only date + explicit credit", async () => {
    await form(unresolvedTransfer()); await change("#date", "2026-02-01"); await submit();
    expect(transactionsApi.updateTransaction).not.toHaveBeenCalled(); expect(container.textContent).toContain(t("transactions.form.reconciliationRequired"));
    await change("#transfer-amount", "3000000.123456"); await submit();
    expect(patch()).toEqual({ date: "2026-02-01", transfer_amount: "3000000.123456" });
  });
  it("successful reconciliation invalidates native and aggregate consumers", async () => {
    const keys = ["accounts", "transactions", "dashboard-summary", "net-worth-summary", "cash-flow", "category-spending-report", "category-ranking", "budget-status", "financial-alerts", "advice"];
    await form(unresolvedTransfer());
    for (const key of keys) client.setQueryData([key, "test"], { unchanged: true });
    client.setQueryData(["goals"], { unchanged: true });
    await change("#transfer-amount", "3000000"); await submit();
    expect(patch()).toEqual({ transfer_amount: "3000000" });
    for (const key of keys) expect(client.getQueryState([key, "test"])?.isInvalidated).toBe(true);
    expect(client.getQueryState(["goals"])?.isInvalidated).toBe(false);
  });
  it.each(["income", "expense"])("transfer → %s restores category UI and clears transfer-only state", async (type) => {
    await form(transferTransaction()); await change("#type", type); expect(container.querySelector("#category")).not.toBeNull();
    expect(container.querySelector("#transfer-amount")).toBeNull(); await submit();
    expect(patch()).toEqual({ type, transfer_account_id: null });
    expect(patch()).not.toHaveProperty("transfer_amount");
  });
  it("expense → transfer explicitly clears its existing category", async () => {
    await form(transferTransaction({ type: "expense", category_id: 5, category, transfer_account_id: null, transfer_account: null, transfer_amount: null, effective_rate: null }));
    await change("#type", "transfer"); await change("#transfer_account", "2"); await change("#transfer-amount", "3000000"); await submit();
    expect(patch()).toEqual({ type: "transfer", category_id: null, transfer_account_id: 2, transfer_amount: "3000000" });
  });
  it("ordinary split → transfer explicitly clears old splits", async () => {
    const tx = transferTransaction({ type: "expense", transfer_account_id: null, transfer_account: null, transfer_amount: null,
      splits: [{ id: 1, category_id: 5, category, amount: "5000", note: null }, { id: 2, category_id: 5, category, amount: "5000", note: null }] });
    await form(tx); await change("#type", "transfer"); await change("#transfer_account", "2"); await change("#transfer-amount", "3000000"); await submit();
    expect(patch()).toEqual({ type: "transfer", transfer_account_id: 2, transfer_amount: "3000000", splits: [] });
  });
  it("ordinary split metadata edit does not resend unchanged splits", async () => {
    const tx = transferTransaction({ type: "expense", transfer_account_id: null, transfer_account: null, transfer_amount: null,
      splits: [{ id: 1, category_id: 5, category, amount: "5000", note: null }, { id: 2, category_id: 5, category, amount: "5000", note: null }] });
    await form(tx); await change("#description", "Receipt"); await submit(); expect(patch()).toEqual({ description: "Receipt" });
  });
  it.each(["1.1234567", "1.0000000", "1e3", "NaN", "Infinity", "-1", "0"])("rejects invalid credit %s without rounding", async (value) => {
    await newTransfer("1", "2", "10000", value); await submit(); expect(transactionsApi.createTransaction).not.toHaveBeenCalled();
    expect(container.textContent).toContain(t("transactions.form.invalidAmount")); expect(container.querySelector<HTMLInputElement>("#transfer-amount")!.value).toBe(value);
  });
  it("rejects invalid source scale and excludes source from destination options", async () => {
    await newTransfer("1", "2", "1.1234567"); await submit(); expect(transactionsApi.createTransaction).not.toHaveBeenCalled();
    expect(container.querySelector('#transfer_account option[value="1"]')).toBeNull();
    await change("#account", "2"); expect(value("#transfer_account")).toBe("");
  });
  it.each([400, 409, 422])("backend %s refetches state without optimistic monetary changes or silent retry", async (code) => {
    const tx = Object.freeze(transferTransaction());
    const fetchTransactions = vi.fn(async () => ({ items: [tx], page: 1, page_size: 20, total: 1 }));
    subscriptions.push(new QueryObserver(client, { queryKey: ["transactions", "test"], queryFn: fetchTransactions }).subscribe(noop));
    await form(tx); await change("#transfer-amount", "3100000");
    vi.mocked(transactionsApi.updateTransaction).mockRejectedValue(new ApiError(code, "category_id is not valid for transfer transactions"));
    await submit(); await settle();
    expect(transactionsApi.updateTransaction).toHaveBeenCalledTimes(1);
    expect(fetchTransactions).toHaveBeenCalledTimes(2); expect(accountsApi.fetchAccounts).toHaveBeenCalledTimes(2);
    expect(tx.transfer_amount).toBe("3000000.000000"); expect(value("#transfer-amount")).toBe("3100000");
    expect(container.textContent).toContain(t("transactions.form.rejected"));
  });
  it("refetched account currency invalidates an entered cross-pair credit", async () => {
    await newTransfer();
    act(() => client.setQueryData(["accounts", false], [transferAccounts[0], transferAccount(2, "USD"), ...transferAccounts.slice(2)])); await settle();
    expect(value("#transfer-amount")).toBe(""); expect(value("#amount")).toBe("10000");
    await submit(); expect(transactionsApi.createTransaction).not.toHaveBeenCalled();
  });
  it("editing archived accounts preserves their stored currencies and amounts", async () => {
    const tx = transferTransaction({ account: { ...transferAccounts[0], id: 9, is_archived: true }, account_id: 9 });
    await form(tx); expect(value("#account")).toBe("9"); expect(value("#transfer-amount")).toBe(tx.transfer_amount);
    await change("#notes", "Archived"); await submit(); expect(patch()).toEqual({ notes: "Archived" });
  });
  it("same → same account pair stays synchronized without destination input", async () => {
    vi.mocked(accountsApi.fetchAccounts).mockResolvedValue([...transferAccounts, transferAccount(5, "USD")]);
    await newTransfer("3", "4", "100.123456"); await change("#transfer_account", "5"); await submit();
    expect(container.querySelector("#transfer-amount")).toBeNull();
    expect(transactionsApi.createTransaction).toHaveBeenCalledWith(expect.objectContaining({ transfer_account_id: 5, transfer_amount: "100.123456" }));
  });
  it("edited cross → same uses exact source; unchanged cross pair keeps credit on source edit", async () => {
    await form(transferTransaction()); await change("#amount", "20000.123456"); await submit();
    expect(patch()).toEqual({ amount: "20000.123456" });
    await change("#account", "3"); await change("#transfer_account", "4"); await submit();
    expect(patch()).toEqual({ account_id: 3, transfer_account_id: 4, amount: "20000.123456", transfer_amount: "20000.123456" });
  });
  it("reconciliation status disappears after the server returns a resolved row", async () => {
    let stored = unresolvedTransfer();
    vi.mocked(transactionsApi.fetchTransactions).mockImplementation(async () => ({ items: [stored], total: 1, page: 1, page_size: 20 }));
    vi.mocked(transactionsApi.updateTransaction).mockImplementation(async (_id, input) => {
      stored = transferTransaction({ transfer_amount: input.transfer_amount! }); return stored;
    });
    await render(<><LiveTransactions /><TransactionFormModal open onClose={noop} transaction={stored} /></>);
    expect(container.querySelector("li")?.textContent).toContain(t("transactions.needsReconciliation"));
    await change("#transfer-amount", "3000000"); await submit(); await settle();
    expect(container.querySelector("li")?.textContent).not.toContain(t("transactions.needsReconciliation"));
    expect(container.querySelector("li")?.textContent).toContain(formatCurrency("3000000", "VND"));
  });
  it.each(["en", "ru"] as const)("%s list and form expose reconciliation and both currencies", async (language) => {
    setLanguage(language); const unresolved = unresolvedTransfer();
    await render(<><TransactionsTable items={[unresolved, transferTransaction({ id: 2 })]} onEdit={noop} onDelete={noop} />
      <TransactionFormModal open onClose={noop} transaction={unresolved} /></>);
    const rows = container.querySelectorAll("li");
    expect(rows[0].textContent).toContain(formatCurrency("10000", "RUB"));
    expect(rows[0].textContent).toContain(t("transactions.needsReconciliation"));
    expect(rows[0].textContent).not.toContain(formatCurrency("0", "VND"));
    expect(rows[1].textContent).toContain(formatCurrency("3000000", "VND"));
    expect(container.querySelector('label[for="transfer-amount"]')?.textContent).toContain(language === "ru" ? "Сумма зачисления" : "Amount received");
    expect(container.querySelector("#category")).toBeNull();
  });
});
