import { moneyToUnits } from "@/lib/money";
import type { Transaction, TransactionInput } from "@/types";

// Mirrors backend transaction_service.FINANCIAL_PATCH_FIELDS. Presence triggers
// final-state validation, even when the submitted value equals the stored one.
const FINANCIAL_FIELDS = [
  "type", "account_id", "transfer_account_id", "amount", "transfer_amount",
  "date", "category_id", "splits",
] as const;

export function hasFinancialChanges(patch: Partial<TransactionInput>): boolean {
  return FINANCIAL_FIELDS.some((key) => Object.hasOwn(patch, key));
}

/** User input: Numeric(20,6), strictly positive, no exponent or silent rounding. */
export function validTransferAmount(value: string): boolean {
  return /^\+?(?:\d+(?:\.\d{0,6})?|\.\d{1,6})$/.test(value)
    && (moneyToUnits(value) ?? 0n) > 0n;
}

/** Display ONLY: destination/source, rounded half-up to 24 fractional digits.
 * Numeric(20,6) positive amounts can yield rates near 1e-20, so 24 places
 * preserve small nonzero rates. No Number conversion, FX lookup or persistence. */
export function transferDisplayRate(source: string, destination: string): string | null {
  if (!validTransferAmount(source) || !validTransferAmount(destination)) return null;
  const debit = moneyToUnits(source)!;
  const credit = moneyToUnits(destination)!;
  const scale = 10n ** 24n;
  const scaled = (credit * scale * 2n + debit) / (debit * 2n);
  const fraction = (scaled % scale).toString().padStart(24, "0").replace(/0+$/, "");
  return `${scaled / scale}${fraction ? `.${fraction}` : ""}`;
}

/** Diff against persisted data, not a full form resubmission. Array metadata
 * excludes read-only IDs/relations; tag ordering has no monetary meaning. */
export function transactionPatch(input: TransactionInput, original: Transaction): Partial<TransactionInput> {
  const before: TransactionInput = {
    type: original.type, account_id: original.account_id, category_id: original.category_id,
    transfer_account_id: original.transfer_account_id, amount: original.amount,
    transfer_amount: original.transfer_amount, date: original.date,
    description: original.description, merchant: original.merchant, notes: original.notes,
    tag_ids: original.tags.map((tag) => tag.id),
    splits: original.splits.map((split) => ({ category_id: split.category_id!, amount: split.amount, note: split.note })),
  };
  const patch: Partial<TransactionInput> = {};
  for (const key of Object.keys(input) as (keyof TransactionInput)[]) {
    const value = input[key];
    if (value === undefined) continue;
    let unchanged = JSON.stringify(value) === JSON.stringify(before[key]);
    if ((key === "amount" || key === "transfer_amount") && typeof value === "string" && typeof before[key] === "string") {
      const units = moneyToUnits(value);
      unchanged = units !== null && units === moneyToUnits(before[key]);
    }
    if (key === "tag_ids") {
      unchanged = JSON.stringify([...(input.tag_ids ?? [])].sort((a, b) => a - b))
        === JSON.stringify([...(before.tag_ids ?? [])].sort((a, b) => a - b));
    }
    if (!unchanged) Object.assign(patch, { [key]: value });
  }
  return patch;
}
