import { Flag, PiggyBank, Pencil, Trash2 } from "lucide-react";
import { formatCurrency, getIntlLocale } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";
import type { Goal } from "@/types";

interface GoalListProps {
  items: Goal[];
  onContribute: (goal: Goal) => void;
  onEdit: (goal: Goal) => void;
  onDelete: (goal: Goal) => void;
}

function formatTargetDate(isoDate: string): string {
  return new Intl.DateTimeFormat(getIntlLocale(), { day: "numeric", month: "short", year: "numeric" }).format(
    new Date(`${isoDate}T00:00:00`)
  );
}

export function GoalList({ items, onContribute, onEdit, onDelete }: GoalListProps) {
  const { t } = useTranslation();

  if (items.length === 0) {
    return <p className="py-10 text-center text-sm text-text-muted">{t("goal.empty")}</p>;
  }

  return (
    <ul className="divide-y divide-gridline">
      {items.map((goal) => {
        const current = goal.current_amount;
        const target = goal.target_amount;
        const remaining = goal.remaining;
        const fillPercent = Math.min(100, goal.percent);

        return (
          <li key={goal.id} className="py-3">
            <div className="flex items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-surface-2">
                <Flag size={16} className="text-text-secondary" />
              </span>
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-text-primary">{goal.name}</span>
              <span className="shrink-0 text-sm tabular-nums text-text-primary">
                {formatCurrency(current, goal.currency)} <span className="text-text-muted">/ {formatCurrency(target, goal.currency)}</span>
              </span>
              <span className="flex shrink-0 gap-1">
                <button
                  type="button"
                  aria-label={t("goal.contributeLabel")}
                  onClick={() => onContribute(goal)}
                  className="rounded-md p-1.5 text-text-muted hover:bg-surface-2 hover:text-success"
                >
                  <PiggyBank size={15} />
                </button>
                <button
                  type="button"
                  aria-label={t("common.edit")}
                  onClick={() => onEdit(goal)}
                  className="rounded-md p-1.5 text-text-muted hover:bg-surface-2 hover:text-text-primary"
                >
                  <Pencil size={15} />
                </button>
                <button
                  type="button"
                  aria-label={t("common.delete")}
                  onClick={() => onDelete(goal)}
                  className="rounded-md p-1.5 text-text-muted hover:bg-surface-2 hover:text-danger"
                >
                  <Trash2 size={15} />
                </button>
              </span>
            </div>
            <div className="mt-2 flex items-center gap-2 pl-12">
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2">
                <span
                  className="block h-full rounded-full bg-success"
                  style={{ width: `${fillPercent}%` }}
                />
              </span>
              <span className="w-10 shrink-0 text-right text-xs tabular-nums text-text-muted">
                {goal.percent.toFixed(0)}%
              </span>
            </div>
            <p className="mt-1 pl-12 text-xs" style={{ color: goal.is_reached ? "var(--success)" : "var(--text-muted)" }}>
              {goal.is_reached
                ? t("goal.reached")
                : goal.target_date
                  ? t("goal.remainingWithDate", { amount: formatCurrency(remaining, goal.currency), date: formatTargetDate(goal.target_date) })
                  : t("goal.remaining", { amount: formatCurrency(remaining, goal.currency) })}
            </p>
          </li>
        );
      })}
    </ul>
  );
}
