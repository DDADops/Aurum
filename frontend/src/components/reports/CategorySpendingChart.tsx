import { analyticsValue, analyticsCoordinate, metricStatus } from "@/lib/analytics";
import { FinancialDataStatus } from "@/components/ui/FinancialDataStatus";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { formatCurrency, getIntlLocale, pluralizeRu } from "@/lib/format";
import { useTranslation, type Language } from "@/lib/i18n";
import { translateCategoryName } from "@/lib/categoryLabels";
import type { CategorySpendingReport } from "@/types";

interface CategorySpendingChartProps {
  report: CategorySpendingReport | undefined;
  isLoading: boolean;
}

function monthKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, "0")}-01`;
}

function formatMonthLabel(key: string): string {
  return new Intl.DateTimeFormat(getIntlLocale(), { month: "short", year: "numeric" }).format(
    new Date(`${key}T00:00:00`)
  );
}

function transactionsCountLabel(count: number, language: Language): string {
  if (language === "ru") return pluralizeRu(count, "транзакция", "транзакции", "транзакций");
  return count === 1 ? "transaction" : "transactions";
}

/** Same landmark idea as the Net Worth chart: year ticks only when the bars
 * span more than one calendar year, so a 5-year report reads at a glance. */
function computeYearTicks(keys: string[]): string[] {
  if (keys.length < 2) return [];
  const startYear = Number(keys[0].slice(0, 4));
  const endYear = Number(keys[keys.length - 1].slice(0, 4));
  if (startYear === endYear) return [];
  const ticks: string[] = [];
  for (let year = startYear + 1; year <= endYear; year++) {
    const jan = `${year}-01-01`;
    if (keys.includes(jan)) ticks.push(jan);
  }
  return ticks;
}

function ChartTooltip({ active, payload, currency }: { currency: string | undefined; active?: boolean; payload?: Array<{ payload: { key: string; amount: number | null; original: string | null } }> }) {
  const { t } = useTranslation();
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm shadow-md">
      <p className="text-text-muted">{formatMonthLabel(point.key)}</p>
      <p className="font-medium text-text-primary">{point.original === null ? t("financial.unknown") : formatCurrency(point.original, currency)}</p>
    </div>
  );
}

export function CategorySpendingChart({ report, isLoading }: CategorySpendingChartProps) {
  const { t, language } = useTranslation();
  const total = analyticsValue(report?.total_amount, metricStatus(report, "total_amount"), report?.currency);
  const average = analyticsValue(report?.average_per_month, metricStatus(report, "average_per_month"), report?.currency);
  const chartData = report?.series.map((point) => ({
    key: monthKey(point.year, point.month),
    amount: analyticsCoordinate(point.amount, point, report.currency),
    original: analyticsValue(point.amount, point, report.currency),
  })) ?? [];
  const yearTicks = computeYearTicks(chartData.map((point) => point.key));

  return (
    <Card>
      <CardHeader className="items-start">
        <div>
          <CardTitle>{report ? translateCategoryName(report.category_name) : t("reports.defaultChartTitle")}</CardTitle>
          <p className="mt-1.5 text-2xl font-semibold tabular-nums text-text-primary sm:text-[28px]">
            {isLoading || !report ? "…" : (total === null ? t("financial.unknown") : formatCurrency(total, report.currency))}
          </p>
          {report && (
            <p className="mt-1 text-sm text-text-muted">
              {t("reports.averagePerMonth", { amount: (average === null ? t("financial.unknown") : formatCurrency(average, report.currency)) })} ·{" "}
              {report.transaction_count} {transactionsCountLabel(report.transaction_count, language)}
            </p>
          )}
        </div>
      </CardHeader>
      {report && <FinancialDataStatus status={report.currency ? report : undefined} className="px-5" />}
      {chartData.some((point) => point.amount === null) && <p className="px-5 text-sm text-text-muted">{t("financial.chartGaps")}</p>}
      <CardContent>
        <div className="h-56 w-full sm:h-64">
          {isLoading || !report || chartData.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-text-muted">
              {isLoading ? t("common.loading") : report && !report.complete ? t("financial.unknown") : t("reports.noDataForPeriod")}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 8, right: 4, bottom: 0, left: 4 }}>
                {yearTicks.length > 0 && (
                  <XAxis
                    dataKey="key"
                    type="category"
                    ticks={yearTicks}
                    tickFormatter={(value: string) => value.slice(0, 4)}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                    interval="preserveStartEnd"
                  />
                )}
                <Tooltip content={<ChartTooltip currency={report?.currency} />} cursor={{ fill: "var(--surface-2)" }} />
                <Bar dataKey="amount" fill={report.category_color} radius={[2, 2, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
        {chartData.length > 1 && (
          <div className="mt-2 flex justify-between text-xs text-text-muted">
            <span>{formatMonthLabel(chartData[0].key)}</span>
            <span>{formatMonthLabel(chartData[chartData.length - 1].key)}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
