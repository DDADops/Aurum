import { PlanningCurrencySelect } from "@/components/ui/PlanningCurrencySelect";
import { useAppSettings } from "@/hooks/useSettings";
import { validPositiveDecimal } from "@/lib/planningMoney";
import { useEffect, useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Input";
import { useCategories } from "@/hooks/useCategories";
import { useCreateBudget, useUpdateBudget } from "@/hooks/useBudgets";
import { useTranslation } from "@/lib/i18n";
import { translateCategoryName } from "@/lib/categoryLabels";
import type { Budget } from "@/types";

interface BudgetFormModalProps {
  open: boolean;
  onClose: () => void;
  budget?: Budget | null;
  /** Expense categories that already have a budget — excluded from the
   * create picker since each category can only carry one budget. */
  excludeCategoryIds: number[];
}

export function BudgetFormModal({ open, onClose, budget, excludeCategoryIds }: BudgetFormModalProps) {
  const { t, language } = useTranslation();
  const { data: categories } = useCategories();
  const createBudget = useCreateBudget();
  const updateBudget = useUpdateBudget();

  const [categoryId, setCategoryId] = useState("");
  const [monthlyLimit, setMonthlyLimit] = useState("");
  const { data: settings } = useAppSettings();
  const [currency, setCurrency] = useState("");
  const [error, setError] = useState<string | null>(null);

  const availableCategories = (categories ?? [])
    .filter((category) => category.kind === "expense" && !excludeCategoryIds.includes(category.id))
    .sort((a, b) => translateCategoryName(a.name).localeCompare(translateCategoryName(b.name), language));

  useEffect(() => {
    if (!open) return;
    setCurrency(budget?.currency ?? settings?.currency ?? "");
    if (budget) {
      setCategoryId(String(budget.category_id));
      setMonthlyLimit(budget.monthly_limit);
    } else {
      setCategoryId(availableCategories[0] ? String(availableCategories[0].id) : "");
      setMonthlyLimit("");
    }
    setError(null);
    // availableCategories is derived from `categories`, not a stable dep — only re-run on open/budget change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, budget]);

  useEffect(() => { if (open && !budget && !currency && settings?.currency) setCurrency(settings.currency); }, [open, settings?.currency, currency, budget]);

  const isSaving = createBudget.isPending || updateBudget.isPending;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (!currency || !validPositiveDecimal(monthlyLimit, 20, 6)) { setError(t("planning.invalidMoney")); return; }
    try {
      if (budget) {
        await updateBudget.mutateAsync({ id: budget.id, monthlyLimit, currency });
      } else {
        await createBudget.mutateAsync({ category_id: Number(categoryId), monthly_limit: monthlyLimit, currency });
      }
      onClose();
    } catch {
      setError(t("budget.form.saveError"));
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={budget ? t("budget.form.editTitle") : t("budget.form.newTitle")}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <PlanningCurrencySelect id="planning-currency" value={currency} onChange={setCurrency} />
        <div>
          <Label htmlFor="budget-category">{t("budget.form.categoryLabel")}</Label>
          {budget ? (
            <p className="text-sm text-text-primary">{translateCategoryName(budget.category_name)}</p>
          ) : availableCategories.length === 0 ? (
            <p className="text-sm text-text-muted">{t("budget.form.noCategoriesAvailable")}</p>
          ) : (
            <Select id="budget-category" required value={categoryId} onChange={(event) => setCategoryId(event.target.value)}>
              {availableCategories.map((category) => (
                <option key={category.id} value={category.id}>
                  {translateCategoryName(category.name)}
                </option>
              ))}
            </Select>
          )}
        </div>

        <div>
          <Label htmlFor="budget-limit">{t("budget.form.limitLabel")}</Label>
          <Input
            id="budget-limit"
            type="text" inputMode="decimal"
            step="0.000001"
            min="0.000001"
            required
            value={monthlyLimit}
            onChange={(event) => setMonthlyLimit(event.target.value)}
          />
        </div>

        {error && <p className="text-sm text-danger">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" disabled={isSaving || (!budget && availableCategories.length === 0)}>
            {isSaving ? t("common.saving") : t("common.save")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
