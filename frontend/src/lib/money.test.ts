import { describe, expect, it } from "vitest";
import { moneyToUnits, splitRemainder, unitsToMoney } from "./money";
import { parseAmount, transactionDedupeKey } from "./csv";

describe("exact monetary input", () => {
  it.each(["0", "-1.123456", "123.45", "99999999999999.999999"])("round trips %s", (value) => {
    expect(unitsToMoney(moneyToUnits(value)!)).toBe(value);
  });
  it.each(["1.1234567", "100000000000000", "NaN", "Infinity", "", "-"])("rejects %s", (value) => {
    expect(moneyToUnits(value)).toBeNull();
  });
  it("compares splits exactly, including beyond Number precision", () => {
    expect(splitRemainder("1.123456", ["0.123456", "1.000000"])).toBe(0n);
    expect(splitRemainder("1.123457", ["0.123456", "1"])).toBe(1n);
    expect(splitRemainder("99999999999999.999999", ["99999999999999", "0.999999"])).toBe(0n);
    expect(splitRemainder("1", ["", "1"])).toBeNull();
  });
  it("preserves CSV precision and distinguishes sub-cent transactions", () => {
    expect(parseAmount("99 999 999 999 999,999999", "comma-decimal")).toBe("99999999999999.999999");
    expect(parseAmount("-1.123456", "dot-decimal")).toBe("-1.123456");
    expect(parseAmount("1.1234567", "dot-decimal")).toBeNull();
    const key = (amount: string) => transactionDedupeKey("2026-01-01", "expense", amount, "Test");
    expect(key("1.123456")).not.toBe(key("1.123457"));
    expect(key("123.450000")).toBe(key("123.45"));
  });
});
