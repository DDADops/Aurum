import { PlanningCurrencySelect } from "@/components/ui/PlanningCurrencySelect";
import { useAppSettings } from "@/hooks/useSettings";
import { validPositiveDecimal } from "@/lib/planningMoney";
import { useEffect, useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Input, Label } from "@/components/ui/Input";
import { useCreateGoal, useUpdateGoal } from "@/hooks/useGoals";
import { useTranslation } from "@/lib/i18n";
import type { Goal } from "@/types";

interface GoalFormModalProps {
  open: boolean;
  onClose: () => void;
  goal?: Goal | null;
}

const EMPTY_FORM = { name: "", target_amount: "", target_date: "" };

export function GoalFormModal({ open, onClose, goal }: GoalFormModalProps) {
  const { t } = useTranslation();
  const createGoal = useCreateGoal();
  const updateGoal = useUpdateGoal();

  const [form, setForm] = useState(EMPTY_FORM);
  const { data: settings } = useAppSettings();
  const [currency, setCurrency] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCurrency(goal?.currency ?? settings?.currency ?? "");
    if (goal) {
      setForm({ name: goal.name, target_amount: goal.target_amount, target_date: goal.target_date ?? "" });
    } else {
      setForm(EMPTY_FORM);
    }
    setError(null);
  }, [open, goal]);

  useEffect(() => { if (open && !goal && !currency && settings?.currency) setCurrency(settings.currency); }, [open, settings?.currency, currency, goal]);

  const isSaving = createGoal.isPending || updateGoal.isPending;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (!currency || !validPositiveDecimal(form.target_amount, 20, 6)) { setError(t("planning.invalidMoney")); return; }
    const input = {
      currency,
      name: form.name,
      target_amount: form.target_amount,
      target_date: form.target_date || null,
    };

    try {
      if (goal) {
        await updateGoal.mutateAsync({ id: goal.id, input });
      } else {
        await createGoal.mutateAsync(input);
      }
      onClose();
    } catch {
      setError(t("goal.form.saveError"));
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={goal ? t("goal.form.editTitle") : t("goal.form.newTitle")}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <PlanningCurrencySelect id="planning-currency" value={currency} onChange={setCurrency} />
        <div>
          <Label htmlFor="goal-name">{t("goal.form.nameLabel")}</Label>
          <Input
            id="goal-name"
            required
            placeholder={t("goal.form.namePlaceholder")}
            value={form.name}
            onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="goal-target">{t("goal.form.targetAmountLabel")}</Label>
            <Input
              id="goal-target"
              type="text" inputMode="decimal"
              step="0.000001"
              min="0.000001"
              required
              value={form.target_amount}
              onChange={(event) => setForm((prev) => ({ ...prev, target_amount: event.target.value }))}
            />
          </div>
          <div>
            <Label htmlFor="goal-date">{t("goal.form.targetDateLabel")}</Label>
            <Input
              id="goal-date"
              type="date"
              value={form.target_date}
              onChange={(event) => setForm((prev) => ({ ...prev, target_date: event.target.value }))}
            />
          </div>
        </div>

        {error && <p className="text-sm text-danger">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" disabled={isSaving}>
            {isSaving ? t("common.saving") : t("common.save")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
