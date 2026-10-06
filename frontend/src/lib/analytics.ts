import { isFinancialDataIncomplete, financialChartValue } from "@/lib/financialData";
import type { FinancialDataStatus, MetricStatus } from "@/types";

type StatusWithMetrics = FinancialDataStatus & { metric_status?: MetricStatus };

export function metricStatus(response: StatusWithMetrics | undefined, metric: string): FinancialDataStatus | undefined {
  return response?.metric_status?.[metric] ?? response;
}

export function analyticsAvailable(status: FinancialDataStatus | undefined, currency: string | undefined): boolean {
  return !!currency && !!status && !isFinancialDataIncomplete(status);
}

export function analyticsValue(value: string | null | undefined, status: FinancialDataStatus | undefined, currency: string | undefined): string | null {
  return analyticsAvailable(status, currency) && value != null && decimalParts(value) !== null ? value : null;
}

/** The only numeric money coordinates supplied to Recharts. API/cache values
 * and tooltip originals remain Decimal strings; missing denomination/status
 * and nullable points remain gaps, never zeros. */
export function analyticsCoordinate(value: string | null | undefined, status: FinancialDataStatus | undefined, currency: string | undefined): number | null {
  return financialChartValue(analyticsValue(value, status, currency));
}

// Aggregate Decimals may exceed a single Numeric(20,6) row's size/scale.
// Exponent is a decimal-position count, not a conversion of money to Number.
function decimalParts(value: string): { units: bigint; scale: number } | null {
  const match = /^([+-]?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(value);
  if (!match) return null;
  const exponent = Number(match[4] ?? "0");
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1000) return null;
  return { units: BigInt(`${match[1]}${match[2]}${match[3] ?? ""}`), scale: (match[3]?.length ?? 0) - exponent };
}

/** Display-only whole percent, rounded half-up using exact Decimal operands.
 * Null means an unavailable ratio (including a zero/negative denominator). */
export function displayPercent(numerator: string | null, denominator: string | null): string | null {
  if (numerator === null || denominator === null) return null;
  const a = decimalParts(numerator), b = decimalParts(denominator);
  if (!a || !b || b.units <= 0n) return null;
  const scale = Math.max(a.scale, b.scale);
  const n = a.units * 10n ** BigInt(scale - a.scale) * 100n;
  const d = b.units * 10n ** BigInt(scale - b.scale);
  const absolute = n < 0n ? -n : n;
  const rounded = (absolute * 2n + d) / (2n * d);
  return `${n < 0n && rounded !== 0n ? "-" : ""}${rounded}`;
}
