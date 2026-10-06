import { Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { formatCurrency } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";

interface RoiProjectionCardProps {
  currency: string | undefined;
  investmentAmount: number;
  annualIncome: number;
  annualRoiPercent: number;
}

const CHART_MAX_YEARS = 30;
const CHART_TICKS = [0, 5, 10, 15, 20, 25, 30];
const TABLE_YEARS = [1, 5, 10, 20, 30];

interface ChartPoint {
  year: number;
  compound: number;
  simple: number;
}

function ChartTooltip({
  currency,
  active,
  payload,
  compoundLabel,
  simpleLabel,
}: {
  currency: string | undefined;
  active?: boolean;
  payload?: Array<{ payload: ChartPoint }>;
  compoundLabel: string;
  simpleLabel: string;
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm shadow-md">
      <p className="text-text-muted">{point.year}</p>
      <p className="font-medium" style={{ color: "var(--series-1)" }}>
        {compoundLabel}: {formatCurrency(point.compound, currency)}
      </p>
      <p className="text-text-muted">
        {simpleLabel}: {formatCurrency(point.simple, currency)}
      </p>
    </div>
  );
}

/** Everything here is derived only from the two numbers typed into
 * RoiCalculatorCard (investment amount, monthly income) — no data from
 * Asset/AssetValuation or anywhere else in the app. Two scenarios,
 * compounded annually from the same starting point:
 *   compound(year) = investment × (1 + annualRoiPercent)^year — the whole
 *     annual return (rent/dividends) is assumed reinvested back into the
 *     same investment every year, so next year's return is earned on a
 *     larger base too.
 *   simple(year) = investment + annualIncome × year — the investment
 *     itself never grows, income is just set aside in cash each year.
 * The gap between the two lines *is* what compounding is worth. */
export function RoiProjectionCard({ currency, investmentAmount, annualIncome, annualRoiPercent }: RoiProjectionCardProps) {
  const { t } = useTranslation();
  const rate = annualRoiPercent / 100;
  const compoundLabel = t("roi.calculator.chartCompoundLabel");
  const simpleLabel = t("roi.calculator.chartSimpleLabel");

  const chartData: ChartPoint[] = Array.from({ length: CHART_MAX_YEARS + 1 }, (_, year) => ({
    year,
    compound: investmentAmount * Math.pow(1 + rate, year),
    simple: investmentAmount + annualIncome * year,
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("roi.calculator.projectionTitle")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-xs text-text-muted">{t("roi.calculator.projectionHint", { percent: annualRoiPercent.toFixed(1) })}</p>

        <div className="h-56 w-full sm:h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 8, right: 4, bottom: 0, left: 4 }}>
              <XAxis
                dataKey="year"
                type="number"
                domain={[0, CHART_MAX_YEARS]}
                ticks={CHART_TICKS}
                axisLine={false}
                tickLine={false}
                tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              />
              <Tooltip content={<ChartTooltip currency={currency} compoundLabel={compoundLabel} simpleLabel={simpleLabel} />} cursor={{ stroke: "var(--gridline)", strokeWidth: 1 }} />
              <Legend
                verticalAlign="top"
                height={24}
                formatter={(value) => (value === "compound" ? compoundLabel : simpleLabel)}
                wrapperStyle={{ fontSize: 12, color: "var(--text-muted)" }}
              />
              <Line
                type="monotone"
                dataKey="compound"
                stroke="var(--series-1)"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="simple"
                stroke="var(--text-muted)"
                strokeWidth={2}
                strokeDasharray="4 4"
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">{t("roi.calculator.tableTitle")}</p>
          <ul className="mt-2 divide-y divide-gridline">
            <li className="flex items-center gap-3 py-2 text-xs font-medium text-text-muted">
              <span className="flex-1" />
              <span className="w-28 shrink-0 text-right">{t("roi.calculator.colCompound")}</span>
              <span className="hidden w-28 shrink-0 text-right sm:block">{t("roi.calculator.colSimple")}</span>
              <span className="w-24 shrink-0 text-right">{t("roi.calculator.colDifference")}</span>
            </li>
            {TABLE_YEARS.map((year) => {
              const compound = investmentAmount * Math.pow(1 + rate, year);
              const simple = investmentAmount + annualIncome * year;
              // Rounded to the cent before comparing sign — compound and simple
              // agree exactly in year 1, but Math.pow's floating-point error
              // can leave a sub-cent residue. `|| 0` also normalizes -0 (which
              // is >= 0 but still formats as "-$0") to a plain positive 0.
              const difference = Math.round((compound - simple) * 100) / 100 || 0;
              return (
                <li key={year} className="flex items-center gap-3 py-2.5">
                  <span className="flex-1 text-sm text-text-secondary">
                    {year === 1 ? t("roi.calculator.yearOne") : t("roi.calculator.yearN", { years: year })}
                  </span>
                  <span className="w-28 shrink-0 text-right text-sm font-medium tabular-nums text-text-primary">
                    {formatCurrency(compound, currency)}
                  </span>
                  <span className="hidden w-28 shrink-0 text-right text-sm tabular-nums text-text-muted sm:block">
                    {formatCurrency(simple, currency)}
                  </span>
                  <span
                    className="w-24 shrink-0 text-right text-sm tabular-nums"
                    style={{ color: difference >= 0 ? "var(--success)" : "var(--danger)" }}
                  >
                    {difference >= 0 ? "+" : ""}
                    {formatCurrency(difference, currency)}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}
