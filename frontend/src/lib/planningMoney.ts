import { analyticsValue, decimalParts, metricStatus } from "./analytics";
import type { Asset, CryptoHolding } from "@/types";

function decimal(units: bigint, scale: number): string {
  if (scale < 0) return String(units * 10n ** BigInt(-scale));
  const sign = units < 0n ? "-" : "";
  const digits = String(units < 0n ? -units : units).padStart(scale + 1, "0");
  return scale ? `${sign}${digits.slice(0, -scale)}.${digits.slice(-scale)}` : `${sign}${digits}`;
}

export function sumDecimals(values: string[]): string {
  const parts = values.map((v) => decimalParts(v));
  if (parts.some((p) => !p)) throw new Error("Invalid Decimal");
  const scale = Math.max(0, ...parts.map((p) => p!.scale));
  return decimal(parts.reduce((sum, p) => sum + p!.units * 10n ** BigInt(scale - p!.scale), 0n), scale);
}

export function compareDecimals(a: string, b: string): number {
  const difference = decimalParts(sumDecimals([a, b.startsWith("-") ? b.slice(1) : `-${b}`]))!.units;
  return difference < 0n ? -1 : difference > 0n ? 1 : 0;
}

/** Rounded display percentage; only the final dimensionless result becomes Number. */
export function percentOf(a: string, b: string, multiplier = 1): number | null {
  const x = decimalParts(a), y = decimalParts(b);
  if (!x || !y || y.units <= 0n) return null;
  const scale = Math.max(x.scale, y.scale);
  const n = x.units * 10n ** BigInt(scale - x.scale) * 10000n * BigInt(multiplier);
  const d = y.units * 10n ** BigInt(scale - y.scale);
  const rounded = ((n < 0n ? -n : n) * 2n + d) / (d * 2n);
  return Number(n < 0n ? -rounded : rounded) / 100;
}

export function validPositiveDecimal(value: string, precision: number, scale: number): boolean {
  if (!/^\d+(?:\.\d+)?$/.test(value)) return false;
  const [whole, fractional = ""] = value.split(".");
  return whole.replace(/^0+/, "").length <= precision - scale
    && fractional.replace(/0+$/, "").length <= scale
    && decimalParts(value)!.units > 0n;
}

export function holdingTotal(holdings: CryptoHolding[], currency: string | undefined, metric: "value" | "cost_basis" | "profit_loss"): string | null {
  if (!currency) return null;
  const values = holdings.map((h) => h.currency === currency ? analyticsValue(h[metric], metricStatus(h, metric), currency) : null);
  return values.some((v) => v === null) ? null : sumDecimals(values as string[]);
}

export function assetRoi(asset: Asset): number | null {
  return asset.currency && asset.valuation_currency_confirmed && asset.valuation_currency === asset.currency
    && asset.monthly_cash_flow !== null && compareDecimals(asset.monthly_cash_flow, "0") > 0
    ? percentOf(asset.monthly_cash_flow, asset.current_value, 12) : null;
}
