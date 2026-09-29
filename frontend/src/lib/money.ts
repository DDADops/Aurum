// Exact input arithmetic for Numeric(20,6). Display formatters remain separate.
const SCALE = 1_000_000n;
const LIMIT = 100_000_000_000_000n * SCALE;

export function moneyToUnits(value: string): bigint | null {
  const match = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(value.trim());
  if (!match || (!match[2] && !match[3])) return null;
  const fraction = (match[3] ?? "").replace(/0+$/, "");
  if (fraction.length > 6) return null;
  const units = BigInt(match[2] || "0") * SCALE + BigInt(fraction.padEnd(6, "0"));
  if (units >= LIMIT) return null;
  return match[1] === "-" ? -units : units;
}

export function unitsToMoney(units: bigint): string {
  const absolute = units < 0n ? -units : units;
  const fraction = String(absolute % SCALE).padStart(6, "0").replace(/0+$/, "");
  return `${units < 0n ? "-" : ""}${absolute / SCALE}${fraction ? `.${fraction}` : ""}`;
}

export function splitRemainder(total: string, parts: string[]): bigint | null {
  const amount = moneyToUnits(total);
  const parsed = parts.map(moneyToUnits);
  if (amount === null || parsed.some((part) => part === null)) return null;
  return amount - parsed.reduce<bigint>((sum, part) => sum + (part ?? 0n), 0n);
}
