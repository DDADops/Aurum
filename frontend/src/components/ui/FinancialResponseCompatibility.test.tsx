import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CashFlowChart } from "@/components/cashflow/CashFlowChart";
import { CategorySpendingChart } from "@/components/reports/CategorySpendingChart";
import { setLanguage } from "@/lib/i18n";
import type { CashFlowResponse, CategorySpendingReport } from "@/types";

const status = {
  currency: "USD", complete: false,
  missing_rates: [{ base_currency: "RUB", quote_currency: "USD", requested_date: "2026-01-15",
    reason: "absent" as const, max_age_days: 7, latest_available_date: null }],
  data_issues: [],
};

describe("transitional aggregate responses", () => {
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

  it("does not render incomplete cash flow as zero or an empty-period fact", () => {
    const response: CashFlowResponse = { ...status, start_date: "2026-01-01", end_date: "2026-01-31",
      total_income: null, total_expense: null, total_net: null, points: [] };
    act(() => root.render(<CashFlowChart cashFlow={response} isLoading={false} />));
    expect(container.textContent).toContain("Exchange rate unavailable");
    expect(container.textContent).not.toContain("$0");
    expect(container.querySelector("svg")).toBeNull();
  });

  it("still displays complete cash flow zero", () => {
    const response: CashFlowResponse = { ...status, complete: true, missing_rates: [],
      start_date: null, end_date: null, total_income: "0", total_expense: "0", total_net: "0", points: [] };
    act(() => root.render(<CashFlowChart cashFlow={response} isLoading={false} />));
    expect(container.textContent).toContain("$0");
    expect(container.textContent).not.toContain("Financial data is incomplete");
  });

  it("does not render an incomplete category report as a zero total", () => {
    const report: CategorySpendingReport = { ...status, category_id: 1, category_name: "Groceries",
      category_color: "#ffffff", category_icon: null, start_date: null, end_date: null,
      total_amount: null, average_per_month: null, transaction_count: 1, series: [] };
    act(() => root.render(<CategorySpendingChart report={report} isLoading={false} />));
    expect(container.textContent).toContain("Financial data is incomplete");
    expect(container.textContent).not.toContain("$0");
  });
});
