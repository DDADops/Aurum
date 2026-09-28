import { describe, expect, it } from "vitest";
import { financialChartValue, knownFinancialValue } from "@/lib/financialData";
import type { FinancialDataStatus, FinancialResponseStatus } from "@/types";

const complete: FinancialDataStatus = { complete: true, missing_rates: [], data_issues: [] };

describe("financial value guards", () => {
  it("preserves real zero, including decimal strings", () => {
    expect(knownFinancialValue(0, complete)).toBe(0);
    expect(knownFinancialValue("0.000000", complete)).toBe("0.000000");
    expect(financialChartValue("0", complete)).toBe(0);
  });

  it.each([null, undefined, "", " ", NaN, Infinity, "invalid"])("does not coerce %s to a financial fact", (value) => {
    expect(knownFinancialValue(value, complete)).toBeNull();
    expect(financialChartValue(value, complete)).toBeNull();
  });

  it("hides a partial number even when it is nonzero", () => {
    expect(knownFinancialValue("123.45", { ...complete, complete: false })).toBeNull();
  });

  it("keeps decimal text intact rather than rounding through Number", () => {
    const value = "12345678901234.123456";
    expect(knownFinancialValue(value)).toBe(value);
  });

  it("supports independent metric statuses without treating a missing metric as zero", () => {
    const response: FinancialResponseStatus<"income" | "expense"> = {
      currency: "USD", complete: false, missing_rates: [], data_issues: [],
      metric_status: { income: complete, expense: { ...complete, complete: false } },
    };
    expect(knownFinancialValue("0", response.metric_status?.income)).toBe("0");
    expect(knownFinancialValue("50", response.metric_status?.expense)).toBeNull();
    expect(knownFinancialValue(undefined)).toBeNull();
  });

  it("treats reported issues as incomplete even with an inconsistent complete flag", () => {
    expect(knownFinancialValue("100", {
      ...complete, data_issues: [{ code: "future_issue_code" }],
    })).toBeNull();
  });
});
