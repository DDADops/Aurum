import { analyticsValue, analyticsCoordinate, metricStatus } from "@/lib/analytics";
import { FinancialDataStatus } from "@/components/ui/FinancialDataStatus";
import { Bar, BarChart, Legend, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { formatCurrency, formatSignedCurrency, getIntlLocale } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";
import type { CashFlowResponse } from "@/types";

interface CashFlowChartProps {
  cashFlow: CashFlowResponse | undefined;
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

/** Same landmark idea as the Net Worth and category-spending charts: year
 * ticks only when the bars span more than one calendar year. */
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

interface ChartPoint {
  key: string;
  income: number | null;
  expense: number | null;
  net: number | null;
  original: { income: string | null; expense: string | null; net: string | null };
}

function ChartTooltip({
  currency,
  active,
  payload,
  incomeLabel,
  expenseLabel,
  netLabel,
}: {
  currency: string | undefined;
  active?: boolean;
  payload?: Array<{ payload: ChartPoint }>;
  incomeLabel: string;
  expenseLabel: string;
  netLabel: string;
}) {
  const { t } = useTranslation();
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm shadow-md">
      <p className="text-text-muted">{formatMonthLabel(point.key)}</p>
      <p className="text-success">
        {incomeLabel}: {point.original.income === null ? t("financial.unknown") : formatCurrency(point.original.income, currency)}
      </p>
      <p className="text-danger">
        {expenseLabel}: {point.original.expense === null ? t("financial.unknown") : formatCurrency(point.original.expense, currency)}
      </p>
      <p className="font-medium text-text-primary">
        {netLabel}: {point.original.net === null ? t("financial.unknown") : formatSignedCurrency(point.original.net, currency)}
      </p>
    </div>
  );
}

export function CashFlowChart({ cashFlow, isLoading }: CashFlowChartProps) {
  const { t } = useTranslation();
  const incomeLabel = t("cashFlow.income");
  const expenseLabel = t("cashFlow.expense");
  const netLabel = t("cashFlow.net");

  const income = analyticsValue(cashFlow?.total_income, metricStatus(cashFlow, "total_income"), cashFlow?.currency);
  const expense = analyticsValue(cashFlow?.total_expense, metricStatus(cashFlow, "total_expense"), cashFlow?.currency);
  const net = analyticsValue(cashFlow?.total_net, metricStatus(cashFlow, "total_net"), cashFlow?.currency);
  const chartData: ChartPoint[] = cashFlow?.points.map((point) => ({
    key: monthKey(point.year, point.month),
    income: analyticsCoordinate(point.income, metricStatus(point, "income"), cashFlow.currency),
    expense: analyticsCoordinate(point.expense, metricStatus(point, "expense"), cashFlow.currency),
    net: analyticsCoordinate(point.net, metricStatus(point, "net"), cashFlow.currency),
    original: {
      income: analyticsValue(point.income, metricStatus(point, "income"), cashFlow.currency),
      expense: analyticsValue(point.expense, metricStatus(point, "expense"), cashFlow.currency),
      net: analyticsValue(point.net, metricStatus(point, "net"), cashFlow.currency),
    },
  })) ?? [];
  const yearTicks = computeYearTicks(chartData.map((point) => point.key));

  return (
    <Card>
      <CardHeader className="items-start">
        <div>
          <CardTitle>{t("nav.cashFlow")}</CardTitle>
          <p className="mt-1.5 text-2xl font-semibold tabular-nums text-text-primary sm:text-[28px]">
            {isLoading || !cashFlow ? "…" : (net === null ? t("financial.unknown") : formatSignedCurrency(net, cashFlow.currency))}
          </p>
          {cashFlow && (
            <p className="mt-1 text-sm">
              <span className="font-medium text-success">
                {incomeLabel} {(income === null ? t("financial.unknown") : formatCurrency(income, cashFlow.currency))}
              </span>
              <span className="text-text-muted"> · </span>
              <span className="font-medium text-danger">
                {expenseLabel} {(expense === null ? t("financial.unknown") : formatCurrency(expense, cashFlow.currency))}
              </span>
            </p>
          )}
        </div>
      </CardHeader>
      {cashFlow && <FinancialDataStatus status={cashFlow.currency ? cashFlow : undefined} className="px-5" />}
      {chartData.some((point) => point.income === null || point.expense === null || point.net === null) && <p className="px-5 text-sm text-text-muted">{t("financial.chartGaps")}</p>}
      <CardContent>
        <div className="h-56 w-full sm:h-64">
          {isLoading || !cashFlow || chartData.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-text-muted">
              {isLoading ? t("common.loading") : cashFlow && !cashFlow.complete ? t("financial.unknown") : t("cashFlow.noData")}
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
                <Tooltip
                  content={<ChartTooltip currency={cashFlow?.currency} incomeLabel={incomeLabel} expenseLabel={expenseLabel} netLabel={netLabel} />}
                  cursor={{ fill: "var(--surface-2)" }}
                />
                <Legend
                  verticalAlign="top"
                  height={24}
                  formatter={(value) => (value === "income" ? incomeLabel : expenseLabel)}
                  wrapperStyle={{ fontSize: 12, color: "var(--text-muted)" }}
                />
                <Bar dataKey="income" fill="var(--success)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
                <Bar dataKey="expense" fill="var(--danger)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
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
