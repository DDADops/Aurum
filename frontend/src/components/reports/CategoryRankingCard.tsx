import { useState } from "react";
import { SquareDivide } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { CategoryBreakdownModal } from "@/components/categories/CategoryBreakdownModal";
import { getCategoryIcon } from "@/lib/icons";
import { formatCurrency } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";
import { translateCategoryName } from "@/lib/categoryLabels";
import type { CategoryRankingItem } from "@/types";

interface CategoryRankingCardProps {
  currency: string | undefined;
  items: CategoryRankingItem[];
  isLoading: boolean;
  selectedCategoryId: number | null;
  onSelectCategory: (categoryId: number) => void;
}

/** Ranks every expense category by total spent over the currently selected
 * period — unlike the Dashboard breakdown (locked to one month) or the
 * detail chart below (locked to one category), this is "which category
 * costs the most" across the whole range at once. Clicking a row drills
 * into that category in the detail chart/transaction list below. */
export function CategoryRankingCard({ currency, items, isLoading, selectedCategoryId, onSelectCategory }: CategoryRankingCardProps) {
  const { t } = useTranslation();
  // The subcategory breakdown lives in a modal, not expanded inline — a
  // category with many subcategories would otherwise push the whole ranking
  // list taller and shift every row below it.
  const [breakdownItem, setBreakdownItem] = useState<CategoryRankingItem | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("reports.categoryRankingTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="py-10 text-center text-sm text-text-muted">{t("common.loading")}</p>
        ) : items.length === 0 ? (
          <p className="py-10 text-center text-sm text-text-muted">{t("reports.noRankingData")}</p>
        ) : (
          <ul className="divide-y divide-gridline">
            {items.map((item, index) => {
              const Icon = getCategoryIcon(item.icon);
              const isSelected = item.category_id === selectedCategoryId;
              const hasChildren = item.children.length > 0;
              return (
                <li key={item.category_id}>
                  <div
                    className={`flex items-center gap-1 rounded-lg transition-colors hover:bg-surface-2 ${
                      isSelected ? "bg-surface-2" : ""
                    }`}
                  >
                    {/* Fixed-width slot on every row, populated or not — the
                        amount column at the row's end must stay flush right
                        the same way whether or not this row has a
                        breakdown to open. */}
                    <span className="ml-1 flex h-8 w-7 shrink-0 items-center justify-center">
                      {hasChildren && (
                        <button
                          type="button"
                          aria-label={t("common.expand")}
                          onClick={() => setBreakdownItem(item)}
                          className="rounded-md p-1.5 text-text-muted hover:text-text-primary"
                        >
                          <SquareDivide size={14} />
                        </button>
                      )}
                    </span>
                    <button
                      type="button"
                      onClick={() => onSelectCategory(item.category_id)}
                      className="flex flex-1 items-center gap-3 py-2.5 text-left"
                    >
                      <span className="w-4 shrink-0 text-right text-xs tabular-nums text-text-muted">{index + 1}</span>
                      <span
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
                        style={{ backgroundColor: `${item.color}26` }}
                      >
                        <Icon size={15} style={{ color: item.color }} />
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm text-text-primary">
                        {translateCategoryName(item.name)}
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
                      <span className="shrink-0 text-sm font-medium tabular-nums text-text-primary">
                        {formatCurrency(item.amount, currency)}
                      </span>
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>

      {breakdownItem && (
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
