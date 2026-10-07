import { holdingTotal, percentOf } from "@/lib/planningMoney";
import type { ReactNode } from "react";
import { Card, CardContent } from "@/components/ui/Card";
import { formatCurrency, maskAmount } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";
import type { CryptoHolding, CryptoRange } from "@/types";

interface CryptoStatsRowProps {
  currency: string | undefined;
  holdings: CryptoHolding[];
  isLoading: boolean;
  hidden: boolean;
  // Which window Best/Worst Performer measures price move over — kept in
  // sync with the History chart's own range selector one level up, so
  // switching one switches both (see CryptoPage).
  range: CryptoRange;
  // Real per-coin 90d % change, fetched on demand only while range==="90d"
  // (see services/crypto_service.py's get_90d_performance) — undefined
  // while that fetch is still in flight.
  performance90d?: Map<number, number | null>;
  isPerformance90dLoading: boolean;
}

interface Totals {
  totalValue: string | null;
  totalCostBasis: string | null;
  totalProfitLoss: string | null;
  totalProfitLossPercent: number | null;
  best: { holding: CryptoHolding; percent: number } | null;
  worst: { holding: CryptoHolding; percent: number } | null;
}

// Best/Worst Performer compare coins against *each other* by market price
// move over the selected window, not against what the user personally paid
// — a coin bought yesterday at the top can still be "best performer" here
// if it's simply up the most of anything held today, independent of entry
// price/timing.
function changePercentFor(
  holding: CryptoHolding,
  range: CryptoRange,
  performance90d: Map<number, number | null> | undefined
): number | null {
  switch (range) {
    case "7d":
      return holding.price_change_7d !== null ? Number(holding.price_change_7d) : null;
    case "30d":
      return holding.price_change_30d !== null ? Number(holding.price_change_30d) : null;
    case "90d":
      return performance90d?.get(holding.asset_id) ?? null;
    case "all":
      return holding.price_change_1y !== null ? Number(holding.price_change_1y) : null;
  }
}

function computeTotals(
  holdings: CryptoHolding[],
  currency: string | undefined,
  range: CryptoRange,
  performance90d: Map<number, number | null> | undefined
): Totals {
  const totalValue = holdingTotal(holdings, currency, "value");
  const totalCostBasis = holdingTotal(holdings, currency, "cost_basis");
  let best: Totals["best"] = null;
  let worst: Totals["worst"] = null;

  for (const holding of holdings) {
    const percent = changePercentFor(holding, range, performance90d);
    if (percent === null) continue;
    if (best === null || percent > best.percent) best = { holding, percent };
    if (worst === null || percent < worst.percent) worst = { holding, percent };
  }

  const totalProfitLoss = holdingTotal(holdings, currency, "profit_loss");
  const totalProfitLossPercent = totalProfitLoss !== null && totalCostBasis !== null ? percentOf(totalProfitLoss, totalCostBasis) : null;

  return { totalValue, totalCostBasis, totalProfitLoss, totalProfitLossPercent, best, worst };
}

function StatTile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Card>
      <CardContent className="p-3.5 sm:p-4">
        <p className="text-xs uppercase tracking-wide text-text-muted">{label}</p>
        <div className="mt-1.5">{children}</div>
      </CardContent>
    </Card>
  );
}

// Same labels CryptoRangeSelector shows on its own pills — "all" is the
// only one actually translated there too.
const RANGE_LABELS: Record<Exclude<CryptoRange, "all">, string> = { "7d": "7D", "30d": "30D", "90d": "90D" };

function PerformerTile({
  label,
  entry,
  rangeLabel,
  isLoading,
}: {
  label: string;
  entry: Totals["best"];
  rangeLabel: string;
  isLoading: boolean;
}) {
  const { t } = useTranslation();
  if (isLoading) {
    return (
      <StatTile label={label}>
        <p className="text-lg font-semibold text-text-muted">{t("common.loading")}</p>
      </StatTile>
    );
  }
  if (entry === null) {
    return (
      <StatTile label={label}>
        <p className="text-lg font-semibold text-text-muted">—</p>
      </StatTile>
    );
  }
  const { holding, percent } = entry;
  const color = percent >= 0 ? "var(--success)" : "var(--danger)";
  return (
    <StatTile label={label}>
      <div className="flex items-center gap-2">
        {holding.thumb_url ? (
          <img src={holding.thumb_url} alt="" className="h-5 w-5 shrink-0 rounded-full" />
        ) : (
          <span className="h-5 w-5 shrink-0 rounded-full bg-surface-2" />
        )}
        <p className="truncate text-lg font-semibold text-text-primary">{holding.symbol}</p>
      </div>
      <p className="mt-0.5 text-sm font-medium tabular-nums" style={{ color }}>
        {percent >= 0 ? "+" : ""}
        {percent.toFixed(2)}%
      </p>
      <p className="mt-0.5 text-xs text-text-muted">{t("crypto.stats.changeLabel", { range: rangeLabel })}</p>
    </StatTile>
  );
}

export function CryptoStatsRow({ currency,
  holdings,
  isLoading,
  hidden,
  range,
  performance90d,
  isPerformance90dLoading,
}: CryptoStatsRowProps) {
  const { t } = useTranslation();

  if (isLoading) {
    return <p className="py-4 text-center text-sm text-text-muted">{t("common.loading")}</p>;
  }
  if (holdings.length === 0) return null;

  const totals = computeTotals(holdings, currency, range, performance90d);
  const profitColor = totals.totalProfitLoss !== null && !totals.totalProfitLoss.startsWith("-") ? "var(--success)" : "var(--danger)";
  const rangeLabel = range === "all" ? t("common.allShort") : RANGE_LABELS[range];
  const performerLoading = range === "90d" && isPerformance90dLoading;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatTile label={t("crypto.stats.allTimeProfit")}>
        <p className="text-lg font-semibold tabular-nums" style={{ color: profitColor }}>
          {maskAmount(totals.totalProfitLoss === null ? t("financial.unknown") : `${totals.totalProfitLoss !== null && !totals.totalProfitLoss.startsWith("-") ? "+" : ""}${formatCurrency(totals.totalProfitLoss, currency)}`, hidden)}
        </p>
        {totals.totalProfitLossPercent !== null && (
          <p className="mt-0.5 text-sm font-medium tabular-nums" style={{ color: profitColor }}>
            {totals.totalProfitLossPercent >= 0 ? "+" : ""}
            {totals.totalProfitLossPercent.toFixed(2)}%
          </p>
        )}
      </StatTile>

      <StatTile label={t("crypto.stats.costBasis")}>
        <p className="text-lg font-semibold tabular-nums text-text-primary">
          {maskAmount(totals.totalCostBasis === null ? t("financial.unknown") : formatCurrency(totals.totalCostBasis, currency), hidden)}
        </p>
        <p className="mt-0.5 text-xs text-text-muted">{t("crypto.stats.costBasisHint")}</p>
      </StatTile>

      <PerformerTile
        label={t("crypto.stats.bestPerformer")}
        entry={totals.best}
        rangeLabel={rangeLabel}
        isLoading={performerLoading}
      />
      <PerformerTile
        label={t("crypto.stats.worstPerformer")}
        entry={totals.worst}
        rangeLabel={rangeLabel}
        isLoading={performerLoading}
      />
    </div>
  );
}
