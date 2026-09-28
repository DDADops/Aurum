import { FinancialDataStatus } from "@/components/ui/FinancialDataStatus";
import { TriangleAlert } from "lucide-react";
import { useFinancialAlerts } from "@/hooks/useInsights";
import { useTranslation, type TranslationKey } from "@/lib/i18n";
import type { FinancialAlert } from "@/types";

// Maps the backend's alert key to the translation template that consumes its params.
const ALERT_MESSAGE_KEYS: Record<string, TranslationKey> = {
  negative_cash_flow_streak: "insights.negativeCashFlow",
  net_worth_decline_streak: "insights.netWorthDecline",
  budget_exceeded: "insights.budgetExceeded",
  risky_allocation_exceeded: "insights.riskyAllocationExceeded",
  idle_cash: "insights.idleCash",
};

function alertMessage(alert: FinancialAlert, t: ReturnType<typeof useTranslation>["t"]): string {
  // "1 categories"/"в 1 категориях" is bad grammar in both languages —
  // singular gets its own pre-written key instead of trying to interpolate
  // a plural-aware word form through a plain {{count}} template.
  if (alert.key === "budget_exceeded" && alert.params.count === 1) {
    return t("insights.budgetExceededOne");
  }
  if (alert.key === "idle_cash" && alert.params.count === 1) {
    return t("insights.idleCashOne", alert.params);
  }
  const messageKey = ALERT_MESSAGE_KEYS[alert.key];
  if (!messageKey) return alert.key;
  return t(messageKey, alert.params);
}

interface AlertBannerProps {
  /** Alert keys to hide on this particular page — e.g. Dashboard omits
   * risky_allocation_exceeded because it reads as too alarming next to the
   * stat cards there; it still shows on Net Worth/Budget where the rest of
   * this banner is used unfiltered. */
  excludeKeys?: string[];
}

/** Proactive early warnings — sustained negative cash flow or a sustained
 * net-worth decline — computed server-side from data that already exists,
 * no configuration. Renders nothing when there's nothing to flag. */
export function AlertBanner({ excludeKeys }: AlertBannerProps = {}) {
  const { data } = useFinancialAlerts();
  const { t } = useTranslation();
  const alerts = (data?.alerts ?? []).filter((alert) => !excludeKeys?.includes(alert.key));

  if (data?.complete === false) return <FinancialDataStatus status={data} />;

  if (alerts.length === 0) return null;

  return (
    <div className="space-y-2">
      {alerts.map((alert) => (
        <div
          key={alert.key}
          className="flex items-start gap-2.5 rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger"
        >
          <TriangleAlert size={16} className="mt-0.5 shrink-0" />
          <span>{alertMessage(alert, t)}</span>
        </div>
      ))}
    </div>
  );
}
