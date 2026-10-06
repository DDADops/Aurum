import { Dialog } from "@/components/ui/Dialog";
import { getCategoryIcon } from "@/lib/icons";
import { formatCurrency } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";
import { translateCategoryName } from "@/lib/categoryLabels";

interface CategoryBreakdownChild {
  category_id: number;
  name: string;
  color: string;
  icon: string | null;
  amount: string;
}

interface CategoryBreakdownModalProps {
  currency: string | undefined;
  open: boolean;
  onClose: () => void;
  categoryId: number;
  categoryName: string;
  totalAmount: string;
  children: CategoryBreakdownChild[];
}

/** Shows how a parent category's total splits across its subcategories — a
 * modal rather than an inline expansion, since a category with many
 * subcategories (or many split-across purchases) would otherwise push the
 * Dashboard's donut/list layout out of alignment as rows grow taller. Shared
 * between the Dashboard breakdown and the Reports category ranking, which
 * expose the same shape (see services/category_rollup.py's
 * CategoryRollupChildItem). */
export function CategoryBreakdownModal({ currency,
  open,
  onClose,
  categoryId,
  categoryName,
  totalAmount,
  children,
}: CategoryBreakdownModalProps) {
  const { t } = useTranslation();
  const total = Number(totalAmount);

  return (
    <Dialog open={open} onClose={onClose} title={translateCategoryName(categoryName)}>
      <div className="mb-3 flex items-center justify-between border-b border-gridline pb-3 text-sm">
        <span className="text-text-muted">{t("reports.categoryBreakdownTotalLabel")}</span>
        <span className="font-semibold tabular-nums text-text-primary">{formatCurrency(totalAmount, currency)}</span>
      </div>
      <ul className="divide-y divide-gridline">
        {children.map((child) => {
          const Icon = getCategoryIcon(child.icon);
          const percent = total ? (Number(child.amount) / total) * 100 : 0;
          const label = child.category_id === categoryId ? t("reports.directSpendLabel") : translateCategoryName(child.name);
          return (
            <li key={child.category_id} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
              <span
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
                style={{ backgroundColor: `${child.color}26` }}
              >
                <Icon size={15} style={{ color: child.color }} />
              </span>
              <span className="min-w-0 flex-1 truncate text-sm text-text-primary">{label}</span>
              <span className="w-9 shrink-0 text-right text-xs text-text-muted tabular-nums">
                {percent.toFixed(0)}%
              </span>
              <span className="shrink-0 text-sm font-medium tabular-nums text-text-primary">
                {formatCurrency(child.amount, currency)}
              </span>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}
