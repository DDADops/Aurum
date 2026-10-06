import { FinancialDataStatus } from "@/components/ui/FinancialDataStatus";
import { analyticsValue, analyticsAvailable, metricStatus, displayPercent } from "@/lib/analytics";
import { useState } from "react";
import { MonthSelector } from "@/components/layout/MonthSelector";
import { YearSelector } from "@/components/layout/YearSelector";
import { StatCard } from "@/components/dashboard/StatCard";
import { SpendingByCategoryCard } from "@/components/dashboard/SpendingByCategoryCard";
import { RecentTransactionsCard } from "@/components/dashboard/RecentTransactionsCard";
import { AlertBanner } from "@/components/insights/AlertBanner";
import { useDashboardSummary } from "@/hooks/useDashboard";
import { useTransactionYears } from "@/hooks/useTransactions";
import { formatCurrency, formatSignedCurrency } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";

export function DashboardPage() {
  const { t } = useTranslation();
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const { data: years } = useTransactionYears();

  const { data, isLoading, isError } = useDashboardSummary(year, month);
  const income = analyticsValue(data?.real_income, metricStatus(data, "real_income"), data?.currency);
  const spent = analyticsValue(data?.spent, metricStatus(data, "spent"), data?.currency);
  const net = analyticsValue(data?.net, metricStatus(data, "net"), data?.currency);
  const rate = displayPercent(net, income);
  const categoryStatus = metricStatus(data, "spending_by_category");

  return (
    <div className="space-y-5">
      <AlertBanner excludeKeys={["risky_allocation_exceeded"]} />

      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <MonthSelector month={month} onChange={setMonth} />
        </div>
        <YearSelector years={years ?? [now.getFullYear()]} year={year} onChange={setYear} />
      </div>

      {isError && (
        <p className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {t("dashboard.errorLoading")}
        </p>
      )}

      {data && !data.complete && <FinancialDataStatus status={data} />}

      <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <StatCard
          label={t("dashboard.statRealIncomeLabel")}
          value={isLoading ? "…" : income === null ? t("financial.unknown") : formatCurrency(income, data?.currency)}
          caption={t("dashboard.statRealIncomeCaption")}
          tone="success"
        />
        <StatCard
          label={t("dashboard.statSpentLabel")}
          value={isLoading ? "…" : spent === null ? t("financial.unknown") : formatCurrency(spent, data?.currency)}
          caption={t("dashboard.statSpentCaption")}
          tone="danger"
        />
        <StatCard
          label={t("dashboard.statNetLabel")}
          value={isLoading ? "…" : net === null ? t("financial.unknown") : formatSignedCurrency(net, data?.currency)}
          caption={t("dashboard.statNetCaption")}
          tone={net === null ? "default" : net.startsWith("-") ? "danger" : "success"}
        />
        <StatCard
          label={t("dashboard.statSavingsRateLabel")}
          value={isLoading ? "…" : rate === null ? "—" : `${rate !== "0" && !rate.startsWith("-") ? "+" : ""}${rate}%`}
          caption={t("dashboard.statSavingsRateCaption")}
          tone={rate === null ? "default" : rate.startsWith("-") ? "danger" : "success"}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.4fr_1fr]">
        {!isLoading && !analyticsAvailable(categoryStatus, data?.currency) ? (
          <FinancialDataStatus status={data?.currency ? categoryStatus : undefined} />
        ) : <SpendingByCategoryCard currency={data?.currency} items={data?.spending_by_category ?? []} />}
        <RecentTransactionsCard year={year} month={month} />
      </div>
    </div>
  );
}
