import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { formatCurrency, getIntlLocale, maskAmount } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";
import type { CryptoHistoryResponse } from "@/types";

interface CryptoHistoryChartBodyProps {
  history: CryptoHistoryResponse | undefined;
  isLoading: boolean;
  hidden: boolean;
}

function formatAxisDate(iso: string): string {
  return new Intl.DateTimeFormat(getIntlLocale(), { month: "short", day: "numeric" }).format(new Date(`${iso}T00:00:00`));
}

function ChartTooltip({
  currency,
  active,
  payload,
  hidden,
}: {
  currency: string | undefined;
  active?: boolean;
  payload?: Array<{ payload: { date: string; value: number } }>;
  hidden: boolean;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm shadow-md">
      <p className="text-text-muted">{formatAxisDate(point.date)}</p>
      <p className="font-medium text-text-primary">{maskAmount(formatCurrency(point.value, currency), hidden)}</p>
    </div>
  );
}

/** The "History" tab's content inside CryptoOverviewCard — same recipe as
 * NetWorthChart's area chart, just without its own Card/header (the total
 * number, eye toggle, and range pills live in the shared header instead). */
export function CryptoHistoryChartBody({ history, isLoading, hidden }: CryptoHistoryChartBodyProps) {
  const { t } = useTranslation();
  const isPositive = history ? Number(history.change_amount) >= 0 : true;
  const trendColor = isPositive ? "var(--success)" : "var(--danger)";
  const chartData = history?.series.map((point) => ({ date: point.date, value: Number(point.value) })) ?? [];

  return (
    <>
      <div className="h-56 w-full sm:h-64">
        {isLoading || chartData.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-text-muted">
            {isLoading ? t("common.loading") : t("netWorth.noChartData")}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 8, right: 4, bottom: 0, left: 4 }}>
              <defs>
                <linearGradient id="cryptoHistoryFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={trendColor} stopOpacity={0.18} />
                  <stop offset="100%" stopColor={trendColor} stopOpacity={0} />
                </linearGradient>
              </defs>
              <YAxis hide domain={["auto", "auto"]} />
              <Tooltip content={<ChartTooltip currency={history?.currency} hidden={hidden} />} cursor={{ stroke: "var(--gridline)", strokeWidth: 1 }} />
              <Area
                type="monotone"
                dataKey="value"
                stroke={trendColor}
                strokeWidth={2}
                fill="url(#cryptoHistoryFill)"
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
    </>
  );
}
