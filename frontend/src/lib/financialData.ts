import type { FinancialDataStatus, FinancialValue } from "@/types";

export function isFinancialDataIncomplete(status: FinancialDataStatus): boolean {
  return !status.complete || status.missing_rates.length > 0 || status.data_issues.length > 0;
}

/** Omitted status supports current APIs; an explicit incomplete status always wins. */
export function knownFinancialValue(
  value: FinancialValue,
  status?: FinancialDataStatus,
): string | number | null {
  if (status && isFinancialDataIncomplete(status)) return null;
  if (value == null || (typeof value === "string" && value.trim() === "")) return null;
  return Number.isFinite(Number(value)) ? value : null;
}

/** Display/chart coordinates only. Never use this conversion for monetary arithmetic. */
export function financialChartValue(value: FinancialValue, status?: FinancialDataStatus): number | null {
  const known = knownFinancialValue(value, status);
  return known === null ? null : Number(known);
}
