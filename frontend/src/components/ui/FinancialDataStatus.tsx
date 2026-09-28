import { useTranslation } from "@/lib/i18n";
import type { TranslationKey } from "@/lib/i18n";
import { isFinancialDataIncomplete } from "@/lib/financialData";
import { cn } from "@/lib/utils";
import type { FinancialDataStatus as Status } from "@/types";

interface FinancialDataStatusProps {
  // No status means an unknown value, not a confirmed zero.
  status?: Status;
  className?: string;
}

export function FinancialDataStatus({ status, className }: FinancialDataStatusProps) {
  const { t } = useTranslation();
  if (status && !isFinancialDataIncomplete(status)) return null;

  const reasons: TranslationKey[] = [];
  for (const rate of status?.missing_rates ?? []) {
    reasons.push(rate.reason === "stale" ? "financial.rateStale" : "financial.rateUnavailable");
  }
  for (const issue of status?.data_issues ?? []) {
    reasons.push(issue.code === "valuation_missing" || issue.code === "price_missing"
      ? "financial.valuationUnavailable"
      : "financial.reconciliation");
  }

  return (
    <span role="status" className={cn("inline-flex flex-wrap items-center gap-x-1 text-sm text-text-secondary", className)}>
      <span>{t(status ? "financial.incomplete" : "financial.unknown")}</span>
      {reasons.length > 0 && <span>· {t(reasons[0])}</span>}
      {reasons.length > 1 && <span>· {t("financial.moreIssues", { count: reasons.length - 1 })}</span>}
    </span>
  );
}
