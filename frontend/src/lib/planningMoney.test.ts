import { describe, expect, it } from "vitest";
import { assetRoi, compareDecimals, holdingTotal, percentOf, sumDecimals, validPositiveDecimal } from "./planningMoney";
import { makeHolding } from "@/test/cryptoFixtures";
import type { Asset } from "@/types";
const ok = { complete: true, missing_rates: [], data_issues: [] };
describe("exact planning money", () => {
  it("adds and compares beyond binary float precision", () => {
    expect(sumDecimals(["100000000000000000.000000000000000001", "0.000000000000000002"])).toBe("100000000000000000.000000000000000003");
    expect(compareDecimals("100000000000000000.01", "100000000000000000.02")).toBe(-1);
    expect(percentOf("1", "3")).toBe(33.33);
    expect(percentOf("1", "0")).toBeNull();
  });
  it.each(["1e3", "NaN", "-1", "0", "1.0000001", "100000000000000"])("rejects invalid Money %s without rounding", (value) => {
    expect(validPositiveDecimal(value, 20, 6)).toBe(false);
  });
  it("accepts precise crypto and six-place money", () => {
    expect(validPositiveDecimal("0.000000000000000001", 38, 18)).toBe(true);
    expect(validPositiveDecimal("0.0000000000000000001", 38, 18)).toBe(false);
    expect(validPositiveDecimal("99999999999999.999999", 20, 6)).toBe(true);
  });
  it("uses metric completeness, preserves zero, rejects partial/mixed denominations", () => {
    const known = makeHolding({ value: "0", complete: false, metric_status: { value: ok } });
    expect(holdingTotal([known], "USD", "value")).toBe("0");
    expect(holdingTotal([known, makeHolding({ value: null })], "USD", "value")).toBeNull();
    expect(holdingTotal([known], "VND", "value")).toBeNull();
    expect(holdingTotal([known], undefined, "value")).toBeNull();
  });
  it("ROI requires compatible confirmed native provenance", () => {
    const asset = { currency: "RUB", valuation_currency: "RUB", valuation_currency_confirmed: true, monthly_cash_flow: "100", current_value: "10000" } as Asset;
    expect(assetRoi(asset)).toBe(12);
    expect(assetRoi({ ...asset, valuation_currency: "USD" })).toBeNull();
    expect(assetRoi({ ...asset, valuation_currency_confirmed: false })).toBeNull();
    expect(assetRoi({ ...asset, current_value: "0" })).toBeNull();
  });
});
