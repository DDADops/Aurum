import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RecurringFormModal } from "./RecurringFormModal";
import { RecurringPage } from "@/pages/RecurringPage";
import * as recurringApi from "@/api/recurring";
import * as accountsApi from "@/api/accounts";
import * as categoriesApi from "@/api/categories";
import { ApiError } from "@/api/client";
import { setLanguage, t } from "@/lib/i18n";
import type { AccountWithBalance, RecurringTransaction } from "@/types";

vi.mock("@/api/recurring");
vi.mock("@/api/accounts");
vi.mock("@/api/categories");
const accounts: AccountWithBalance[] = ["RUB", "VND", "RUB"].map((currency, i) => ({
  id: i + 1, name: `Account ${i + 1}`, currency, type: "checking", is_archived: false,
  has_monetary_history: false, balance: "0", color: "#123456", complete: true, missing_rates: [], data_issues: [],
}));
const template: RecurringTransaction = {
  id: 1, account_id: 1, account_name: "Account 1", transfer_account_id: 2, transfer_account_name: "Account 2",
  category_id: null, category_name: null, category_color: null, category_icon: null,
  type: "transfer", amount: "123.000001", amount_currency: "RUB", transfer_amount: "456.123456", transfer_amount_currency: "VND",
  description: "Transfer", merchant: null, notes: null, frequency: "monthly", anchor_date: "2026-01-01",
  last_posted_date: null, is_active: true, next_due_date: "2026-01-01", is_due: true, days_until_due: -1,
};
let client: QueryClient;
let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const close = vi.fn();
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.resetAllMocks(); setLanguage("en");
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  vi.mocked(accountsApi.fetchAccounts).mockResolvedValue(accounts);
  vi.mocked(categoriesApi.fetchCategories).mockResolvedValue([]);
  vi.mocked(recurringApi.fetchRecurring).mockResolvedValue([template]);
  vi.mocked(recurringApi.createRecurring).mockResolvedValue(template);
  vi.mocked(recurringApi.updateRecurring).mockResolvedValue(template);
  vi.mocked(recurringApi.postRecurring).mockResolvedValue(template);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount()); client.clear(); container.remove(); setLanguage("ru");
  vi.unstubAllGlobals(); Reflect.deleteProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT");
});
async function settle() { await act(async () => { await new Promise((r) => setTimeout(r, 20)); }); }
async function render(node: ReactNode) {
  await act(async () => root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)); await settle();
}
async function form(row?: RecurringTransaction) { await render(<RecurringFormModal open onClose={close} recurring={row} />); }
function input(id: string, value: string) {
  const el = container.querySelector<HTMLInputElement>(`#recurring-${id}`)!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function select(id: string, value: string) {
  const el = container.querySelector<HTMLSelectElement>(`#recurring-${id}`)!;
  act(() => { el.value = value; el.dispatchEvent(new Event("change", { bubbles: true })); });
}
function reenter() { act(() => Array.from(container.querySelectorAll("button")).find((b) => b.textContent === t("recurring.editMoney"))!.click()); }
async function submit() {
  await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); }); await settle();
}
async function changeCurrency(id: number, currency: string) {
  const next = accounts.map((a) => a.id === id ? { ...a, currency } : a);
  vi.mocked(accountsApi.fetchAccounts).mockResolvedValue(next);
  act(() => client.setQueryData(["accounts", true], next)); await settle();
}
it("creates same-currency transfers using backend normalization", async () => {
  await form(); select("type", "transfer"); select("transfer-account", "3");
  input("amount", "123.000001"); input("description", "Same"); await submit();
  expect(recurringApi.createRecurring).toHaveBeenCalledWith(expect.objectContaining({ account_id: 1, transfer_account_id: 3,
    type: "transfer", amount: "123.000001", amount_currency: "RUB", transfer_amount: null, transfer_amount_currency: "RUB", category_id: null }));
});
it("creates exact cross-currency amounts without any FX request", async () => {
  const fetch = vi.fn(() => { throw new Error("No reference FX request"); }); vi.stubGlobal("fetch", fetch);
  await form(); select("type", "transfer"); select("transfer-account", "2");
  input("amount", "99999999999999.999999"); input("transfer-amount", "90071992547409.123456"); input("description", "Exact"); await submit();
  expect(recurringApi.createRecurring).toHaveBeenCalledWith(expect.objectContaining({ amount: "99999999999999.999999", amount_currency: "RUB", transfer_amount: "90071992547409.123456", transfer_amount_currency: "VND" }));
  expect(fetch).not.toHaveBeenCalled();
});
it("edits cross-currency instructions with both explicitly re-entered amounts", async () => {
  await form(template); reenter();
  expect(container.querySelector<HTMLInputElement>("#recurring-amount")!.value).toBe("");
  input("amount", "200.123456"); input("transfer-amount", "70000.123456"); await submit();
  expect(recurringApi.updateRecurring).toHaveBeenCalledWith(1, { type: "transfer", account_id: 1, category_id: null,
    transfer_account_id: 2, amount: "200.123456", amount_currency: "RUB", transfer_amount: "70000.123456", transfer_amount_currency: "VND" });
});
it("sends only changed metadata, excluding every monetary/snapshot field", async () => {
  await form(template); input("description", "Renamed"); input("merchant", "Merchant"); select("frequency", "weekly");
  input("anchor-date", "2026-02-01"); await submit();
  expect(recurringApi.updateRecurring).toHaveBeenCalledWith(1, { description: "Renamed", merchant: "Merchant", frequency: "weekly", anchor_date: "2026-02-01" });
});
it.each([1, 2])("preserves stored denominations after account %s changes currency", async (id) => {
  await form(template); await changeCurrency(id, "USD");
  expect(container.querySelector('label[for="recurring-amount"]')!.textContent).toContain("RUB");
  expect(container.querySelector('label[for="recurring-transfer-amount"]')!.textContent).toContain("VND");
  expect(container.querySelector<HTMLInputElement>("#recurring-transfer-amount")!.value).toBe(template.transfer_amount);
  input("description", "Metadata only"); await submit();
  expect(recurringApi.updateRecurring).toHaveBeenCalledWith(1, { description: "Metadata only" });
});
it.each([1, 2])("reconciles account %s currency only after explicit re-entry", async (id) => {
  await form(template); await changeCurrency(id, "USD"); reenter(); await submit();
  expect(recurringApi.updateRecurring).not.toHaveBeenCalled();
  input("amount", "12.000001"); input("transfer-amount", "34.000001"); await submit();
  expect(recurringApi.updateRecurring).toHaveBeenCalledWith(1, expect.objectContaining({ amount: "12.000001", transfer_amount: "34.000001",
    amount_currency: id === 1 ? "USD" : "RUB", transfer_amount_currency: id === 2 ? "USD" : "VND" }));
});
it("does not silently refresh captured currency intent while money is being entered", async () => {
  await form(template); reenter(); input("amount", "12"); input("transfer-amount", "34");
  await changeCurrency(2, "USD"); await submit();
  expect(recurringApi.updateRecurring).not.toHaveBeenCalled(); expect(close).not.toHaveBeenCalled();
  expect(container.textContent).toContain(t("recurring.currencyMismatch"));
  expect(container.querySelector<HTMLInputElement>("#recurring-transfer-amount")!.value).toBe("34");
});
it("clears amounts on pair changes and normalizes a switch to same currency", async () => {
  await form(template); reenter(); input("amount", "12"); input("transfer-amount", "34"); select("transfer-account", "3");
  expect(container.querySelector<HTMLInputElement>("#recurring-amount")!.value).toBe("");
  input("amount", "15.000001"); await submit();
  expect(recurringApi.updateRecurring).toHaveBeenCalledWith(1, expect.objectContaining({ amount: "15.000001", transfer_amount: null, transfer_amount_currency: "RUB" }));
});
it("permits metadata-only editing of an unresolved legacy transfer", async () => {
  await form({ ...template, transfer_amount: null, transfer_amount_currency: null });
  input("description", "Keep unknown"); await submit();
  expect(recurringApi.updateRecurring).toHaveBeenCalledWith(1, { description: "Keep unknown" });
});
it.each([400, 409, 422])("handles form %s without losing input and refetches accounts", async (status) => {
  await form(template); input("description", "Keep draft");
  vi.mocked(recurringApi.updateRecurring).mockRejectedValue(new ApiError(status, status === 422 ? "recurring_snapshot_must_match_account" : "invalid"));
  const calls = vi.mocked(accountsApi.fetchAccounts).mock.calls.length; await submit();
  expect(close).not.toHaveBeenCalled(); expect(container.querySelector('[role="alert"]')).not.toBeNull();
  expect(container.querySelector<HTMLInputElement>("#recurring-description")!.value).toBe("Keep draft");
  expect(vi.mocked(accountsApi.fetchAccounts).mock.calls.length).toBeGreaterThan(calls);
});
it.each([400, 409, 422])("shows posting %s and never optimistically marks the item posted", async (status) => {
  await render(<RecurringPage />);
  vi.mocked(recurringApi.postRecurring).mockRejectedValue(new ApiError(status, status === 409 ? "recurring_currency_mismatch" : "invalid"));
  const reads = vi.mocked(recurringApi.fetchRecurring).mock.calls.length;
  await act(async () => container.querySelector<HTMLButtonElement>(`button[aria-label="${t("recurring.postLabel")}"]`)!.click()); await settle();
  expect(container.querySelector('[role="alert"]')!.textContent).toBe(t(status === 409 ? "recurring.currencyMismatch" : "recurring.postError"));
  expect(client.getQueryData<RecurringTransaction[]>(["recurring"])![0].last_posted_date).toBeNull();
  expect(client.getQueryData<RecurringTransaction[]>(["recurring"])![0].is_due).toBe(true);
  expect(vi.mocked(recurringApi.fetchRecurring).mock.calls.length).toBeGreaterThan(reads);
  expect(recurringApi.postRecurring).toHaveBeenCalledTimes(1);
});
it.each(["ru", "en"] as const)("renders stored transfer currencies and localized instructions in %s", async (language) => {
  setLanguage(language); await render(<RecurringPage />);
  expect(container.querySelector("li")!.textContent).toContain("₽");
  expect(container.querySelector("li")!.textContent).toContain("₫");
  act(() => container.querySelector<HTMLButtonElement>(`button[aria-label="${t("common.edit")}"]`)!.click()); await settle();
  expect(container.textContent).toContain(t("recurring.editMoney"));
});
it("creates ordinary expense with source snapshot and no destination denomination", async () => {
  await form(); input("amount", "0.000001"); input("description", "Expense"); await submit();
  expect(recurringApi.createRecurring).toHaveBeenCalledWith(expect.objectContaining({ type: "expense", amount: "0.000001", amount_currency: "RUB",
    transfer_account_id: null, transfer_amount: null, transfer_amount_currency: null }));
});
