import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { getCategoryIcon } from "@/lib/icons";
import { formatCurrency } from "@/lib/format";
import { useTranslation, type TranslationKey } from "@/lib/i18n";
import type { NetWorthBreakdownItem } from "@/types";

interface AssetAllocationCardProps {
  currency: string | undefined;
  breakdown: NetWorthBreakdownItem[];
  total: string | null;
  isLoading: boolean;
}

// Breakdown items come from the backend with a machine-readable `key`
// ("cash" or an AssetClass value) — translate the label locally instead of
// showing the backend's (Russian-only) pre-rendered `name`.
function breakdownLabelKey(key: string): TranslationKey {
  return `netWorth.assetClass.${key}` as TranslationKey;
}

export function AssetAllocationCard({ currency, breakdown, total, isLoading }: AssetAllocationCardProps) {
  const { t } = useTranslation();

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {t("netWorth.assetsTitlePrefix")} · {total === null ? t("financial.unknown") : formatCurrency(total, currency)}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="py-10 text-center text-sm text-text-muted">{t("common.loading")}</p>
        ) : (
          <>
            <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-surface-2">
              {breakdown.map((item) =>
                item.percent > 0 ? (
                  <div
                    key={item.key}
                    style={{ width: `${item.percent}%`, backgroundColor: item.color }}
                    className="h-full first:rounded-l-full last:rounded-r-full"
                  />
                ) : null
              )}
            </div>

            <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-text-secondary">
              {breakdown.map((item) => (
                <li key={item.key} className="flex items-center gap-1.5">
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: item.color }} />
                  {t(breakdownLabelKey(item.key))} {item.percent.toFixed(0)}%
                </li>
              ))}
            </ul>

            <ul className="mt-4 divide-y divide-gridline border-t border-gridline">
              {breakdown.map((item) => {
                const Icon = getCategoryIcon(item.icon);
                return (
                  <li key={item.key} className="flex items-center gap-3 py-2.5 first:pt-3">
                    <span
                      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
                      style={{ backgroundColor: `${item.color}26` }}
                    >
                      <Icon size={15} style={{ color: item.color }} />
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm text-text-primary">
                      {t(breakdownLabelKey(item.key))}
                    </span>
                    <span className="hidden w-24 shrink-0 items-center gap-2 sm:flex">
                      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
                        <span
                          className="block h-full rounded-full"
                          style={{ width: `${item.percent}%`, backgroundColor: item.color }}
                        />
                      </span>
                      <span className="w-9 shrink-0 text-right text-xs text-text-muted tabular-nums">
                        {item.percent.toFixed(0)}%
                      </span>
                    </span>
                    <span className="w-14 shrink-0 text-right text-xs text-text-muted tabular-nums sm:hidden">
                      {item.percent.toFixed(0)}%
                    </span>
                    <span className="shrink-0 text-sm font-medium tabular-nums text-text-primary">
                      {formatCurrency(item.amount, currency)}
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  );
}
