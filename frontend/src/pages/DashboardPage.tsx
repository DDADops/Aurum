import { financialChartValue, knownFinancialValue } from "@/lib/financialData";
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

/** Share of income left over after spending (net / real_income). `null` when
 * there was no income to take a share of, rather than a misleading 0%. */
function savingsRate(realIncome: number, net: number): number | null {
  return realIncome > 0 ? (net / realIncome) * 100 : null;
}

function formatPercent(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(0)}%`;
}

export function DashboardPage() {
  const { t } = useTranslation();
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const { data: years } = useTransactionYears();

  const { data, isLoading, isError } = useDashboardSummary(year, month);
  const income = knownFinancialValue(data?.real_income);
  const spent = knownFinancialValue(data?.spent);
  const net = knownFinancialValue(data?.net);
  const incomeNumber = financialChartValue(income);
  const netNumber = financialChartValue(net);
  const rate = incomeNumber !== null && netNumber !== null ? savingsRate(incomeNumber, netNumber) : null;

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

      <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        <StatCard
          label={t("dashboard.statRealIncomeLabel")}
          value={isLoading ? "…" : income === null ? t("financial.unknown") : formatCurrency(income)}
          caption={t("dashboard.statRealIncomeCaption")}
          tone="success"
        />
        <StatCard
          label={t("dashboard.statSpentLabel")}
          value={isLoading ? "…" : spent === null ? t("financial.unknown") : formatCurrency(spent)}
          caption={t("dashboard.statSpentCaption")}
          tone="danger"
        />
        <StatCard
          label={t("dashboard.statNetLabel")}
          value={isLoading ? "…" : net === null ? t("financial.unknown") : formatSignedCurrency(net)}
          caption={t("dashboard.statNetCaption")}
          tone={netNumber === null ? "default" : netNumber >= 0 ? "success" : "danger"}
        />
        <StatCard
          label={t("dashboard.statSavingsRateLabel")}
          value={isLoading ? "…" : rate === null ? "—" : formatPercent(rate)}
          caption={t("dashboard.statSavingsRateCaption")}
          tone={rate === null ? "default" : rate >= 0 ? "success" : "danger"}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.4fr_1fr]">
        <SpendingByCategoryCard items={data?.spending_by_category ?? []} />
        <RecentTransactionsCard year={year} month={month} />
      </div>
    </div>
  );
}
