import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssetFormModal } from "./AssetFormModal";
import * as assetsApi from "@/api/assets";
import * as settingsApi from "@/api/settings";
import { ApiError } from "@/api/client";
import { setLanguage, t } from "@/lib/i18n";
import type { Asset } from "@/types";

vi.mock("@/api/assets");
vi.mock("@/api/settings");
const settings = { currency: "USD", negative_cash_flow_threshold_months: 3, net_worth_decline_threshold_months: 3,
  risky_allocation_threshold_percent: 70, idle_cash_threshold_amount: "10000", idle_cash_threshold_days: 90, app_version: "test" };
const asset: Asset = { id: 1, name: "House", asset_class: "other", currency: "RUB", has_monetary_history: true,
  current_value: "123.123456789123456789", valuation_currency: "RUB", valuation_currency_confirmed: true,
  monthly_cash_flow: "100.000001", notes: null, capital_role: "income", risk_level: "low", as_of_date: "2026-01-01" };
let client: QueryClient;
let container: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const close = vi.fn();
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.resetAllMocks(); setLanguage("en");
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
  client.setQueryData(["settings"], settings);
  vi.mocked(settingsApi.fetchSettings).mockResolvedValue(settings);
  vi.mocked(assetsApi.fetchAssets).mockResolvedValue([asset]);
  vi.mocked(assetsApi.createAsset).mockResolvedValue(asset);
  vi.mocked(assetsApi.updateAsset).mockResolvedValue(asset);
  vi.mocked(assetsApi.addAssetValuation).mockResolvedValue(asset);
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount()); client.clear(); container.remove(); setLanguage("ru");
  Reflect.deleteProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT");
});
async function settle() { await act(async () => { await new Promise((r) => setTimeout(r, 20)); }); }
async function render(row?: Asset) {
  if (row) vi.mocked(assetsApi.fetchAssets).mockResolvedValue([row]);
  await act(async () => root.render(<QueryClientProvider client={client}><AssetFormModal open asset={row} onClose={close} /></QueryClientProvider>));
  await settle();
}
function input(id: string, value: string) {
  const el = container.querySelector<HTMLInputElement>(`#${id}`)!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
function select(value: string) {
  const el = container.querySelector<HTMLSelectElement>("#asset-currency")!;
  act(() => { el.value = value; el.dispatchEvent(new Event("change", { bubbles: true })); });
}
async function submit() {
  await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  await settle();
}
it("creates RUB explicitly with exact Decimal strings and freezes settings default", async () => {
  client.setQueryData(["settings"], { ...settings, currency: "RUB" });
  await render();
  act(() => client.setQueryData(["settings"], { ...settings, currency: "VND" })); await settle();
  input("asset-name", "Exact"); input("asset-value", "12345678901234567890.123456789123456789");
  input("asset-cash-flow", "99999999999999.999999"); await submit();
  expect(assetsApi.createAsset).toHaveBeenCalledWith(expect.objectContaining({ currency: "RUB", value: "12345678901234567890.123456789123456789", monthly_cash_flow: "99999999999999.999999" }));
});
it("retains selected RUB through Settings USD -> VND", async () => {
  await render(); select("RUB");
  act(() => client.setQueryData(["settings"], { ...settings, currency: "VND" })); await settle();
  expect(container.querySelector<HTMLSelectElement>("#asset-currency")!.value).toBe("RUB");
});
it("edits existing RUB independently of Settings and locks history", async () => {
  await render(asset);
  act(() => client.setQueryData(["settings"], { ...settings, currency: "VND" })); await settle();
  const selector = container.querySelector<HTMLSelectElement>("#asset-currency")!;
  expect(selector.value).toBe("RUB"); expect(selector.disabled).toBe(true);
  await submit(); expect(assetsApi.addAssetValuation).not.toHaveBeenCalled();
  expect(assetsApi.updateAsset).toHaveBeenCalledWith(1, expect.not.objectContaining({ currency: expect.anything() }));
});
it.each(["replace", "clear"])("requires explicit cash flow %s on no-history currency change", async (action) => {
  await render({ ...asset, has_monetary_history: false }); select("VND"); await submit();
  expect(assetsApi.updateAsset).not.toHaveBeenCalled();
  expect(container.textContent).toContain(t("netWorth.form.cashFlowReplacement"));
  if (action === "replace") input("asset-cash-flow", "987.000001");
  else act(() => Array.from(container.querySelectorAll("button")).find((b) => b.textContent === t("netWorth.form.clearCashFlow"))!.click());
  await submit();
  expect(assetsApi.updateAsset).toHaveBeenCalledWith(1, expect.objectContaining({ currency: "VND", monthly_cash_flow: action === "replace" ? "987.000001" : null }));
});
it("allows no-history currency change with no existing cash flow", async () => {
  await render({ ...asset, has_monetary_history: false, monthly_cash_flow: null }); select("VND"); await submit();
  expect(assetsApi.updateAsset).toHaveBeenCalledWith(1, expect.objectContaining({ currency: "VND", monthly_cash_flow: null }));
});
it("keeps legacy unknown valuation unknown on metadata-only save", async () => {
  await render({ ...asset, valuation_currency: null, valuation_currency_confirmed: false });
  const provenance = container.querySelector('[data-testid="asset-valuation-provenance"]')!.textContent;
  expect(provenance).toContain(t("netWorth.form.unknownValuation")); expect(provenance).not.toContain("RUB");
  input("asset-name", "Renamed"); await submit(); expect(assetsApi.addAssetValuation).not.toHaveBeenCalled();
});
it("shows a confirmed foreign valuation in its actual denomination", async () => {
  await render({ ...asset, valuation_currency: "VND" });
  expect(container.querySelector('[data-testid="asset-valuation-provenance"]')!.textContent).toContain("VND");
});
it("posts exact valuation with form intent even when refetch changes currency between requests", async () => {
  await render({ ...asset, has_monetary_history: false, monthly_cash_flow: null });
  input("asset-value", "100000.123456789123456789");
  vi.mocked(assetsApi.fetchAssets).mockResolvedValue([{ ...asset, currency: "VND" }]);
  await submit();
  expect(assetsApi.addAssetValuation).toHaveBeenCalledWith(1, expect.objectContaining({ value: "100000.123456789123456789", expected_currency: "RUB" }));
});
it.each([400, 409, 422])("keeps modal open and refetches on valuation %s after metadata succeeds", async (status) => {
  await render(asset); input("asset-value", "100000");
  vi.mocked(assetsApi.addAssetValuation).mockRejectedValue(new ApiError(status, status === 409 ? "asset_currency_mismatch" : "invalid"));
  const calls = vi.mocked(assetsApi.fetchAssets).mock.calls.length;
  await submit();
  expect(close).not.toHaveBeenCalled(); expect(container.querySelector("form")).not.toBeNull();
  expect(container.textContent).toContain(t("netWorth.form.partialSave"));
  if (status === 409) expect(container.textContent).toContain(t("netWorth.form.currencyMismatch"));
  expect(vi.mocked(assetsApi.fetchAssets).mock.calls.length).toBeGreaterThan(calls);
  expect(assetsApi.addAssetValuation).toHaveBeenCalledTimes(1);
  expect(container.querySelector<HTMLInputElement>("#asset-value")!.value).toBe("100000");
});
it("handles concurrent history rejection without relabelling cached Asset", async () => {
  await render({ ...asset, has_monetary_history: false, monthly_cash_flow: null }); select("VND");
  vi.mocked(assetsApi.updateAsset).mockRejectedValue(new ApiError(409, "currency_change_requires_empty_history"));
  vi.mocked(assetsApi.fetchAssets).mockResolvedValue([asset]); await submit();
  expect(close).not.toHaveBeenCalled(); expect(assetsApi.addAssetValuation).not.toHaveBeenCalled();
  expect(client.getQueryData<Asset[]>(["assets"])![0].currency).toBe("RUB");
  expect(container.textContent).toContain(t("netWorth.form.currencyHistory"));
  expect(container.textContent).not.toContain(t("netWorth.form.partialSave"));
});
it.each(["ru", "en"] as const)("localizes currency intent UI in %s", async (language) => {
  setLanguage(language); await render(asset);
  expect(container.querySelector('label[for="asset-currency"]')!.textContent).toBe(t("netWorth.form.currency"));
});

it("requires re-entry of valuation after native currency selection changes", async () => {
  await render({ ...asset, has_monetary_history: false, monthly_cash_flow: null });
  input("asset-value", "100000"); select("VND");
  expect(container.querySelector<HTMLInputElement>("#asset-value")!.value).toBe("");
  input("asset-value", "234.123456789123456789"); await submit();
  expect(assetsApi.addAssetValuation).toHaveBeenCalledWith(1, expect.objectContaining({ expected_currency: "VND", value: "234.123456789123456789" }));
});
it("restores original cash flow when an unsaved currency choice is reverted", async () => {
  await render({ ...asset, has_monetary_history: false }); select("VND"); select("RUB"); await submit();
  expect(assetsApi.updateAsset).toHaveBeenCalledWith(1, expect.not.objectContaining({ monthly_cash_flow: expect.anything() }));
  expect(container.querySelector<HTMLInputElement>("#asset-cash-flow")!.value).toBe(asset.monthly_cash_flow);
});

async function externalCurrencyChange() {
  const newer = { ...asset, currency: "VND", has_monetary_history: false, monthly_cash_flow: "700" };
  vi.mocked(assetsApi.fetchAssets).mockResolvedValue([newer]);
  act(() => client.setQueryData(["assets"], [newer]));
  await settle();
}
it("saves metadata after external RUB -> VND without reverting currency or cash flow", async () => {
  await render({ ...asset, has_monetary_history: false });
  input("asset-name", "Renamed"); await externalCurrencyChange(); await submit();
  expect(assetsApi.updateAsset).toHaveBeenCalledWith(1, {
    name: "Renamed", asset_class: "other", notes: null, capital_role: "income", risk_level: "low",
  });
  expect(assetsApi.addAssetValuation).not.toHaveBeenCalled();
  expect(client.getQueryData<Asset[]>(["assets"])![0].currency).toBe("VND");
  expect(container.textContent).toContain(t("netWorth.form.staleCurrency"));
});
it("rejects explicit RUB -> USD when authoritative currency changed to VND", async () => {
  await render({ ...asset, has_monetary_history: false }); select("USD");
  input("asset-cash-flow", "12.000001"); await externalCurrencyChange();
  const calls = vi.mocked(assetsApi.fetchAssets).mock.calls.length;
  await submit();
  expect(assetsApi.updateAsset).not.toHaveBeenCalled(); expect(assetsApi.addAssetValuation).not.toHaveBeenCalled();
  expect(close).not.toHaveBeenCalled();
  expect(container.textContent).toContain(t("netWorth.form.staleCurrency"));
  expect(container.querySelector<HTMLSelectElement>("#asset-currency")!.value).toBe("USD");
  expect(container.querySelector<HTMLInputElement>("#asset-cash-flow")!.value).toBe("12.000001");
  expect(vi.mocked(assetsApi.fetchAssets).mock.calls.length).toBeGreaterThan(calls);
});
it.each(["asset-cash-flow", "asset-value"])("preserves stale %s input without reinterpreting it as VND", async (field) => {
  await render({ ...asset, has_monetary_history: false }); input(field, "123.000001");
  await externalCurrencyChange(); await submit();
  expect(assetsApi.updateAsset).not.toHaveBeenCalled(); expect(assetsApi.addAssetValuation).not.toHaveBeenCalled();
  expect(close).not.toHaveBeenCalled();
  expect(container.querySelector<HTMLInputElement>(`#${field}`)!.value).toBe("123.000001");
  expect(container.textContent).toContain(t("netWorth.form.staleCurrency"));
});
it("keeps initialization baseline when the asset prop itself is refreshed", async () => {
  await render({ ...asset, has_monetary_history: false }); select("USD"); input("asset-cash-flow", "12");
  await externalCurrencyChange();
  await render({ ...asset, has_monetary_history: false, currency: "VND" });
  await submit(); expect(assetsApi.updateAsset).not.toHaveBeenCalled();
  expect(container.textContent).toContain(t("netWorth.form.staleCurrency"));
});
