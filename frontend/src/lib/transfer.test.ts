import { describe, expect, it } from "vitest";
import { hasFinancialChanges, transactionPatch, transferDisplayRate, validTransferAmount } from "./transfer";
import { unresolvedTransfer } from "@/test/transactionFixtures";
import type { TransactionInput } from "@/types";

describe("display-only exact transfer rate", () => {
  it.each([
    ["10000", "3000000", "300"], ["3000000", "120", "0.00004"],
    ["3", "1", "0.333333333333333333333333"], ["3", "2", "0.666666666666666666666667"],
    ["0.000001", "99999999999999.999999", "99999999999999999999"],
    ["99999999999999.999999", "0.000001", "0.00000000000000000001"],
    ["100.123456", "100.123456", "1"],
  ])("%s → %s gives %s", (source, destination, expected) => {
    const amounts = Object.freeze({ source, destination });
    expect(transferDisplayRate(amounts.source, amounts.destination)).toBe(expected);
    expect(amounts).toEqual({ source, destination });
  });
  it.each(["", "0", "-1", "1e3", "NaN", "Infinity", "1.1234567", "1.0000000", "100000000000000"])("rejects %s", (value) => {
    expect(validTransferAmount(value)).toBe(false);
    expect(transferDisplayRate("1", value)).toBeNull();
    expect(transferDisplayRate(value, "1")).toBeNull();
  });
});

describe("partial PATCH contract", () => {
  it.each(["type", "account_id", "transfer_account_id", "amount", "transfer_amount", "date", "category_id", "splits"])("%s is financial even when null", (field) => {
    expect(hasFinancialChanges({ [field]: null })).toBe(true);
  });
  it.each(["description", "merchant", "notes", "tag_ids"])("%s is nonfinancial", (field) => {
    expect(hasFinancialChanges({ [field]: null })).toBe(false);
  });
  it("does not resend unchanged financial fields, amount formatting or tag order", () => {
    const original = unresolvedTransfer();
    original.tags = [{ id: 1, name: "A" }, { id: 2, name: "B" }];
    const input: TransactionInput = { type: original.type, account_id: 1, transfer_account_id: 2,
      category_id: null, amount: "10000", transfer_amount: null, date: original.date,
      description: "ATM cash", tag_ids: [2, 1], splits: undefined };
    expect(transactionPatch(input, original)).toEqual({ description: "ATM cash" });
  });
});
