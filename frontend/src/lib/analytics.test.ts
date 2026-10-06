import { describe, expect, it } from "vitest";
import { analyticsValue, analyticsCoordinate, displayPercent, metricStatus } from "./analytics";
const complete = { complete: true, missing_rates: [], data_issues: [] };
describe("analytics monetary boundaries", () => {
  it.each([null, undefined])("keeps %s as a gap", (value) => {
    expect(analyticsValue(value, complete, "USD")).toBeNull();
    expect(analyticsCoordinate(value, complete, "USD")).toBeNull();
  });
  it("preserves full Decimal strings and true zero until the coordinate boundary", () => {
    const amount = "99999999999999999.123456789012345678";
    expect(analyticsValue(amount, complete, "VND")).toBe(amount);
    expect(analyticsCoordinate("0", complete, "VND")).toBe(0);
    expect(analyticsCoordinate(amount, complete, "VND")).toBeTypeOf("number");
  });
  it("requires denomination and complete status even for nonzero data", () => {
    expect(analyticsCoordinate("20", { ...complete, complete: false }, "USD")).toBeNull();
    expect(analyticsValue("20", complete, undefined)).toBeNull();
    expect(analyticsValue("20", undefined, "USD")).toBeNull();
  });
  it("uses independent metric status before the response status", () => {
    const response = { ...complete, complete: false, metric_status: { income: complete } };
    expect(analyticsValue("50", metricStatus(response, "income"), "RUB")).toBe("50");
    expect(analyticsValue("50", metricStatus(response, "expense"), "RUB")).toBeNull();
  });
  it.each([
    ["1", "3", "33"], ["2", "3", "67"], ["-1", "3", "-33"],
    ["99999999999999.999999", "99999999999999.999999", "100"],
    ["0E-18", "10", "0"], ["1E-18", "2E-18", "50"],
    ["1", "0", null], [null, "10", null], ["1", null, null],
  ])("exact display percent %s / %s", (part, total, result) => {
    expect(displayPercent(part, total)).toBe(result);
  });
});
