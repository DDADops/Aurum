import { formatCurrency } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";
import type { Transaction } from "@/types";

/** Keep the known debit visible even when the destination needs reconciliation. */
export function TransactionAmount({ transaction: tx }: { transaction: Transaction }) {
  const { t } = useTranslation();
  const source = <>{formatCurrency(tx.amount, tx.account.currency)} {tx.account.currency}</>;
  if (tx.type !== "transfer") return <>{tx.type === "expense" ? "-" : "+"}{source}</>;
  const destination = tx.transfer_account;
  if (tx.transfer_amount === null || !destination) {
    return <>{source}<span className="block text-xs" role="status">→ {t("transactions.needsReconciliation")}</span></>;
  }
  if (tx.account.currency === destination.currency) return source;
  return <>{source}<span className="block">→ {formatCurrency(tx.transfer_amount, destination.currency)} {destination.currency}</span></>;
}
