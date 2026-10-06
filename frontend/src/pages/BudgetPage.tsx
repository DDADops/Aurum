import { useState } from "react";
import { Plus } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { MonthSelector } from "@/components/layout/MonthSelector";
import { YearSelector } from "@/components/layout/YearSelector";
import { AlertBanner } from "@/components/insights/AlertBanner";
import { BudgetList } from "@/components/budget/BudgetList";
import { BudgetFormModal } from "@/components/budget/BudgetFormModal";
import { useBudgets, useBudgetStatus, useDeleteBudget } from "@/hooks/useBudgets";
import { useTransactionYears } from "@/hooks/useTransactions";
import { useTranslation } from "@/lib/i18n";
import { translateCategoryName } from "@/lib/categoryLabels";
import type { Budget, BudgetStatus } from "@/types";

export function BudgetPage() {
  const { t } = useTranslation();
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const { data: years } = useTransactionYears();

  const { data: budgets } = useBudgets();
  const { data: status, isLoading } = useBudgetStatus(year, month);
  const deleteBudget = useDeleteBudget();

  const [modalOpen, setModalOpen] = useState(false);
  const [editingBudget, setEditingBudget] = useState<Budget | null>(null);

  function openCreateModal() {
    setEditingBudget(null);
    setModalOpen(true);
  }

  function openEditModal(item: BudgetStatus) {
    setEditingBudget({
      id: item.budget_id,
      currency: item.currency,
      category_id: item.category_id,
      category_name: item.category_name,
      category_color: item.category_color,
      category_icon: item.category_icon,
      monthly_limit: item.monthly_limit,
    });
    setModalOpen(true);
  }

  function handleDelete(item: BudgetStatus) {
    if (window.confirm(t("budget.confirmDelete", { name: translateCategoryName(item.category_name) }))) {
      deleteBudget.mutate(item.budget_id);
    }
  }

  return (
    <div className="space-y-5">
      <AlertBanner />

      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <MonthSelector month={month} onChange={setMonth} />
        </div>
        <YearSelector years={years ?? [now.getFullYear()]} year={year} onChange={setYear} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("nav.budget")}</CardTitle>
          <Button onClick={openCreateModal}>
            <Plus size={16} />
            {t("common.add")}
          </Button>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <p className="py-10 text-center text-sm text-text-muted">{t("common.loading")}</p>
          ) : (
            <BudgetList items={status?.items ?? []} onEdit={openEditModal} onDelete={handleDelete} />
          )}
        </CardContent>
      </Card>

      <BudgetFormModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        budget={editingBudget}
        excludeCategoryIds={budgets?.map((budget) => budget.category_id) ?? []}
      />
    </div>
  );
}
