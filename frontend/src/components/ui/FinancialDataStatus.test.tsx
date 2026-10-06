import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { FinancialDataStatus } from "@/components/ui/FinancialDataStatus";
import { setLanguage } from "@/lib/i18n";
import type { FinancialDataStatus as Status, MissingRate } from "@/types";

const rate: MissingRate = {
  base_currency: "RUB", quote_currency: "USD", requested_date: "2026-09-01",
  reason: "absent", max_age_days: 7, latest_available_date: null,
};
const complete: Status = { complete: true, missing_rates: [], data_issues: [] };

describe("FinancialDataStatus", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    setLanguage("en");
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    setLanguage("ru");
    Reflect.deleteProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT");
  });

  function render(status?: Status) {
    act(() => root.render(<FinancialDataStatus status={status} />));
    return container.textContent;
  }

  it("renders no warning for complete data", () => {
    expect(render(complete)).toBe("");
  });

  it("uses an accessible unknown state without inventing a zero", () => {
    expect(render()).toBe("Value unknown");
    expect(container.querySelector('[role="status"]')).not.toBeNull();
  });

  it.each(["absent", "stale", "precision_unrepresentable"] as const)("represents %s FX", (reason) => {
    const text = render({ ...complete, complete: false, missing_rates: [{ ...rate, reason }] });
    expect(text).toContain("Financial data is incomplete");
    expect(text).toContain(reason === "stale" ? "Exchange rate is too old" : reason === "precision_unrepresentable" ? "Exchange rate cannot be represented at the supported precision" : "Exchange rate unavailable");
  });

  it("bounds multiple issues and safely handles future codes", () => {
    const text = render({ ...complete, complete: false,
      data_issues: Array.from({ length: 100 }, () => ({ code: "future_issue" })),
    });
    expect(text).toContain("Financial record requires reconciliation");
    expect(text).toContain("Other reasons: 99");
    expect(text?.length).toBeLessThan(160);
  });

  it("shows valuation failures in Russian and updates when the language changes", () => {
    render({ ...complete, complete: false, data_issues: [{ code: "valuation_missing" }] });
    act(() => setLanguage("ru"));
    expect(container.textContent).toContain("Оценка стоимости недоступна");
  });

  it("never presents an incomplete status as a complete monetary total", () => {
    expect(render({ ...complete, complete: false })).toBe("Financial data is incomplete");
    expect(container.textContent).not.toMatch(/[$%]|\b0\b/);
  });
});
