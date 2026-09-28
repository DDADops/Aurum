import { FinancialDataStatus } from "@/components/ui/FinancialDataStatus";
import { financialChartValue, knownFinancialValue } from "@/lib/financialData";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowDown, ArrowUp } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { RangeSelector } from "@/components/layout/RangeSelector";
import { formatCurrency, formatSignedCurrency, getIntlLocale } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";
import type { NetWorthRange, NetWorthSummary } from "@/types";

interface NetWorthChartProps {
  summary: NetWorthSummary | undefined;
  isLoading: boolean;
  range: NetWorthRange;
  onRangeChange: (range: NetWorthRange) => void;
}

function formatAxisDate(iso: string): string {
  return new Intl.DateTimeFormat(getIntlLocale(), { month: "short", day: "numeric", year: "numeric" }).format(
    new Date(`${iso}T00:00:00`)
  );
}

/** Jan-1 of every year strictly inside the range, so a multi-year chart gets
 * a year landmark on its axis instead of just two endpoint dates — lets you
 * place "5 years ago" without hovering pixel-by-pixel. Single-year ranges
 * return an empty array and the axis stays hidden, unchanged from before. */
function computeYearTicks(dates: string[]): string[] {
  if (dates.length < 2) return [];
  const start = dates[0];
  const end = dates[dates.length - 1];
  const startYear = Number(start.slice(0, 4));
  const endYear = Number(end.slice(0, 4));
  if (startYear === endYear) return [];

  const ticks: string[] = [];
  for (let year = startYear + 1; year <= endYear; year++) {
    const jan1 = `${year}-01-01`;
    if (jan1 >= start && jan1 <= end) ticks.push(jan1);
  }
  return ticks;
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: { date: string; value: number | null } }> }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  if (point.value === null) return null;
  return (
    <div className="rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm shadow-md">
      <p className="text-text-muted">{formatAxisDate(point.date)}</p>
      <p className="font-medium text-text-primary">{formatCurrency(point.value)}</p>
    </div>
  );
}

export function NetWorthChart({ summary, isLoading, range, onRangeChange }: NetWorthChartProps) {
  const { t } = useTranslation();
  const current = knownFinancialValue(summary?.current, summary);
  const change = financialChartValue(summary?.change_amount, summary);
  const isPositive = change !== null && change >= 0;
  const trendColor = change === null ? "var(--text-muted)" : isPositive ? "var(--success)" : "var(--danger)";
  const chartData = summary?.series.map((point) => ({ date: point.date, value: financialChartValue(point.value, point) })) ?? [];
  const yearTicks = computeYearTicks(chartData.map((point) => point.date));

  return (
    <Card>
      <CardHeader className="items-start">
        <div>
          <CardTitle>{t("nav.netWorth")}</CardTitle>
          <p className="mt-1.5 text-2xl font-semibold tabular-nums text-text-primary sm:text-[28px]">
            {isLoading ? "…" : current === null ? t("financial.unknown") : formatCurrency(current, summary?.currency)}
          </p>
          {summary && change !== null && (
            <p
              className="mt-1 flex items-center gap-1 text-sm font-medium"
              style={{ color: trendColor }}
            >
              {isPositive ? <ArrowUp size={14} /> : <ArrowDown size={14} />}
              {formatSignedCurrency(change, summary.currency)}
              {summary.change_percent != null && ` (${isPositive ? "+" : ""}${summary.change_percent.toFixed(1)}%)`}
              <span className="font-normal text-text-muted">{t("netWorth.periodSuffix")}</span>
            </p>
          )}
        </div>
        <RangeSelector value={range} onChange={onRangeChange} />
      </CardHeader>
      {summary && !summary.complete && <FinancialDataStatus status={summary} className="px-5" />}
      <CardContent>
        <div className="h-56 w-full sm:h-64">
          {isLoading || chartData.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-text-muted">
              {isLoading ? t("common.loading") : t("netWorth.noChartData")}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 8, right: 4, bottom: 0, left: 4 }}>
                <defs>
                  <linearGradient id="netWorthFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={trendColor} stopOpacity={0.18} />
                    <stop offset="100%" stopColor={trendColor} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <YAxis hide domain={["auto", "auto"]} />
                {yearTicks.length > 0 && (
                  <XAxis
                    dataKey="date"
                    type="category"
                    ticks={yearTicks}
                    tickFormatter={(value: string) => value.slice(0, 4)}
                    axisLine={false}
                    tickLine={false}
                    tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                    interval="preserveStartEnd"
                  />
                )}
                <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--gridline)", strokeWidth: 1 }} />
                <Area
                  connectNulls={false}
                  type="monotone"
                  dataKey="value"
                  stroke={trendColor}
                  strokeWidth={2}
                  fill="url(#netWorthFill)"
                  isAnimationActive={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
        {chartData.length > 1 && (
          <div className="mt-2 flex justify-between text-xs text-text-muted">
            <span>{formatAxisDate(chartData[0].date)}</span>
            <span>{formatAxisDate(chartData[chartData.length - 1].date)}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
