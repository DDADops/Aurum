import type { AccountWithBalance, Transaction } from "@/types";

export function transferAccount(id: number, currency: string): AccountWithBalance {
  return { id, currency, name: `Account ${id}`, type: "checking", color: null, is_archived: false,
    balance: "0", has_monetary_history: false, complete: true, missing_rates: [], data_issues: [] };
}
export const transferAccounts = [transferAccount(1, "RUB"), transferAccount(2, "VND"), transferAccount(3, "USD"), transferAccount(4, "USD")];
export function transferTransaction(overrides: Partial<Transaction> = {}): Transaction {
  return { id: 1, type: "transfer", account_id: 1, account: transferAccounts[0],
    transfer_account_id: 2, transfer_account: transferAccounts[1], amount: "10000.000000",
    transfer_amount: "3000000.000000", effective_rate: "300", category_id: null, category: null,
    description: "Transfer", merchant: null, notes: null, date: "2026-01-15", tags: [], splits: [],
    complete: true, missing_rates: [], data_issues: [], ...overrides };
}
export function unresolvedTransfer(): Transaction {
  return transferTransaction({ transfer_amount: null, effective_rate: null, complete: false,
    data_issues: [{ code: "transfer_amount_unresolved" }] });
}
