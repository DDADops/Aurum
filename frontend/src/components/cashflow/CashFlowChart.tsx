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
  income: number;
  expense: number;
  net: number;
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
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm shadow-md">
      <p className="text-text-muted">{formatMonthLabel(point.key)}</p>
      <p className="text-success">
        {incomeLabel}: {formatCurrency(point.income, currency)}
      </p>
      <p className="text-danger">
        {expenseLabel}: {formatCurrency(point.expense, currency)}
      </p>
      <p className="font-medium text-text-primary">
        {netLabel}: {formatSignedCurrency(point.net, currency)}
      </p>
    </div>
  );
}

export function CashFlowChart({ cashFlow, isLoading }: CashFlowChartProps) {
  const { t } = useTranslation();
  const incomeLabel = t("cashFlow.income");
  const expenseLabel = t("cashFlow.expense");
  const netLabel = t("cashFlow.net");

  if (cashFlow && (!cashFlow.complete || cashFlow.total_income === null ||
      cashFlow.total_expense === null || cashFlow.total_net === null)) {
    return <FinancialDataStatus status={cashFlow} />;
  }

  const chartData: ChartPoint[] =
    cashFlow?.points.map((point) => ({
      key: monthKey(point.year, point.month),
      income: Number(point.income),
      expense: Number(point.expense),
      net: Number(point.net),
    })) ?? [];
  const yearTicks = computeYearTicks(chartData.map((point) => point.key));

  return (
    <Card>
      <CardHeader className="items-start">
        <div>
          <CardTitle>{t("nav.cashFlow")}</CardTitle>
          <p className="mt-1.5 text-2xl font-semibold tabular-nums text-text-primary sm:text-[28px]">
            {isLoading || !cashFlow ? "…" : (cashFlow.total_net === null ? t("financial.unknown") : formatSignedCurrency(cashFlow.total_net, cashFlow.currency))}
          </p>
          {cashFlow && (
            <p className="mt-1 text-sm">
              <span className="font-medium text-success">
                {incomeLabel} {(cashFlow.total_income === null ? t("financial.unknown") : formatCurrency(cashFlow.total_income, cashFlow.currency))}
              </span>
              <span className="text-text-muted"> · </span>
              <span className="font-medium text-danger">
                {expenseLabel} {(cashFlow.total_expense === null ? t("financial.unknown") : formatCurrency(cashFlow.total_expense, cashFlow.currency))}
              </span>
            </p>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <div className="h-56 w-full sm:h-64">
          {isLoading || !cashFlow || chartData.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-text-muted">
              {isLoading ? t("common.loading") : t("cashFlow.noData")}
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
