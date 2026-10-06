import { useState } from "react";
import { SquareDivide } from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { CategoryBreakdownModal } from "@/components/categories/CategoryBreakdownModal";
import { getCategoryIcon } from "@/lib/icons";
import { formatCurrency } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";
import { translateCategoryName } from "@/lib/categoryLabels";
import type { CategoryBreakdownItem } from "@/types";

interface SpendingByCategoryCardProps {
  currency: string | undefined;
  items: CategoryBreakdownItem[];
}

function DonutTooltip({ active, payload, currency }: { currency: string | undefined; active?: boolean; payload?: Array<{ payload: CategoryBreakdownItem }> }) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface-1 px-3 py-2 text-sm shadow-md">
      <p className="font-medium text-text-primary">{translateCategoryName(item.name)}</p>
      <p className="text-text-secondary">
        {formatCurrency(item.amount, currency)} · {item.percent.toFixed(1)}%
      </p>
    </div>
  );
}

export function SpendingByCategoryCard({ currency, items }: SpendingByCategoryCardProps) {
  const { t } = useTranslation();
  // The breakdown lives in a modal, not expanded inline — a category with
  // many subcategories (or many split purchases) would otherwise push this
  // row taller than the fixed-height donut next to it, throwing the
  // side-by-side layout out of alignment.
  const [breakdownItem, setBreakdownItem] = useState<CategoryBreakdownItem | null>(null);
  const hasData = items.length > 0;
  // Recharts needs a numeric dataKey — API amounts arrive as strings (Decimal
  // is serialized as string to avoid float precision loss).
  const chartData = items.map((item) => ({ ...item, amount: Number(item.amount) }));

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("dashboard.spendingByCategoryTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        {!hasData ? (
          <p className="py-10 text-center text-sm text-text-muted">{t("dashboard.noExpensesThisMonth")}</p>
        ) : (
          <div className="flex flex-col items-center gap-6 sm:flex-row sm:items-center">
            <div className="h-56 w-56 shrink-0 sm:h-64 sm:w-64">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={chartData}
                    dataKey="amount"
                    nameKey="name"
                    innerRadius="62%"
                    outerRadius="100%"
                    paddingAngle={2}
                    stroke="var(--surface-1)"
                    strokeWidth={2}
                    isAnimationActive={false}
                  >
                    {items.map((item) => (
                      <Cell key={item.category_id ?? "other"} fill={item.color} />
                    ))}
                  </Pie>
                  <Tooltip content={<DonutTooltip currency={currency} />} />
                </PieChart>
              </ResponsiveContainer>
            </div>

            <ul className="w-full min-w-0 flex-1 divide-y divide-gridline">
              {items.map((item) => {
                const Icon = getCategoryIcon(item.icon);
                const hasChildren = item.children.length > 0;
                return (
                  <li key={item.category_id ?? "other"} className="flex items-center gap-2 py-2 first:pt-0 last:pb-0">
                    {/* Fixed-width slot on every row, populated or not — the
                        amount column at the row's end must stay flush right
                        the same way whether or not this row has a
                        breakdown to open. */}
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center">
                      {hasChildren && (
                        <button
                          type="button"
                          aria-label={t("common.expand")}
                          onClick={() => setBreakdownItem(item)}
                          className="rounded-md p-0.5 text-text-muted hover:bg-surface-2 hover:text-text-primary"
                        >
                          <SquareDivide size={15} />
                        </button>
                      )}
                    </span>
                    <span
                      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
                      style={{ backgroundColor: `${item.color}26` }}
                    >
                      <Icon size={14} style={{ color: item.color }} />
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm text-text-primary">
                      {translateCategoryName(item.name)}
                    </span>
                    <span className="shrink-0 text-sm font-medium tabular-nums text-text-primary">
                      {formatCurrency(item.amount, currency)}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </CardContent>

      {breakdownItem && breakdownItem.category_id !== null && (
        <CategoryBreakdownModal currency={currency}
          open
          onClose={() => setBreakdownItem(null)}
          categoryId={breakdownItem.category_id}
          categoryName={breakdownItem.name}
          totalAmount={breakdownItem.amount}
          children={breakdownItem.children}
        />
      )}
    </Card>
  );
}
