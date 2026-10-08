import { useEffect, useRef, useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Input";
import { useAccounts } from "@/hooks/useAccounts";
import { useCategories } from "@/hooks/useCategories";
import { useCreateRecurring, useUpdateRecurring } from "@/hooks/useRecurring";
import { useTranslation } from "@/lib/i18n";
import { translateCategoryName } from "@/lib/categoryLabels";
import { recurringErrorKey } from "@/lib/recurring";
import type { RecurringFrequency, RecurringTransaction, RecurringTransactionInput, RecurringTransactionUpdateInput, TransactionType } from "@/types";

interface RecurringFormModalProps {
  open: boolean;
  onClose: () => void;
  recurring?: RecurringTransaction | null;
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

const EMPTY_FORM = {
  type: "expense" as TransactionType,
  account_id: "",
  category_id: "",
  transfer_account_id: "",
  amount: "",
  amount_currency: "",
  transfer_amount: "",
  transfer_amount_currency: "",
  description: "",
  merchant: "",
  frequency: "monthly" as RecurringFrequency,
  anchor_date: todayIso(),
};

export function RecurringFormModal({ open, onClose, recurring }: RecurringFormModalProps) {
  const { t } = useTranslation();
  const { data: accounts, refetch: refetchAccounts } = useAccounts(true);
  const initialized = useRef(false);
  const original = useRef<RecurringTransaction | null>(null);
  const [editMoney, setEditMoney] = useState(false);
  const { data: categories } = useCategories();
  const createRecurring = useCreateRecurring();
  const updateRecurring = useUpdateRecurring();

  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) { initialized.current = false; return; }
    if (initialized.current || (!recurring && !accounts)) return;
    initialized.current = true;
    original.current = recurring ? { ...recurring } : null;
    setEditMoney(!recurring);
    if (recurring) {
      setForm({
        type: recurring.type,
        account_id: String(recurring.account_id),
        category_id: recurring.category_id ? String(recurring.category_id) : "",
        transfer_account_id: recurring.transfer_account_id ? String(recurring.transfer_account_id) : "",
        amount: recurring.amount,
        amount_currency: recurring.amount_currency,
        transfer_amount: recurring.transfer_amount ?? "",
        transfer_amount_currency: recurring.transfer_amount_currency ?? "",
        description: recurring.description,
        merchant: recurring.merchant ?? "",
        frequency: recurring.frequency,
        anchor_date: recurring.anchor_date,
      });
    } else {
      const first = accounts?.find((account) => !account.is_archived);
      setForm({ ...EMPTY_FORM, anchor_date: todayIso(), account_id: first ? String(first.id) : "", amount_currency: first?.currency ?? "" });
    }
    setError(null);
  }, [open, recurring, accounts]);

  const source = accounts?.find((account) => String(account.id) === form.account_id);
  const destination = accounts?.find((account) => String(account.id) === form.transfer_account_id);
  const isTransfer = form.type === "transfer";
  const crossCurrency = isTransfer && form.amount_currency !== form.transfer_amount_currency;
  const mismatch = !!accounts && (!source || source.currency !== form.amount_currency
    || (isTransfer && (!destination || destination.currency !== form.transfer_amount_currency || (!editMoney && !form.transfer_amount))));

  function reenterMoney() {
    setEditMoney(true);
    setForm((prev) => ({ ...prev, amount: "", transfer_amount: "",
      amount_currency: source?.currency ?? "", transfer_amount_currency: destination?.currency ?? "" }));
    setError(null);
  }

  function changePair(accountId: string, destinationId: string, type = form.type) {
    setForm((prev) => ({ ...prev, type, account_id: accountId, transfer_account_id: destinationId,
      category_id: "", amount: "", transfer_amount: "",
      amount_currency: accounts?.find((a) => String(a.id) === accountId)?.currency ?? "",
      transfer_amount_currency: accounts?.find((a) => String(a.id) === destinationId)?.currency ?? "" }));
  }

  const relevantCategories = (categories ?? []).filter((category) =>
    form.type === "income" ? category.kind === "income" : category.kind === "expense"
  );

  const isSaving = createRecurring.isPending || updateRecurring.isPending;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    const baseline = original.current;
    const metadata = {
      description: form.description,
      merchant: form.merchant || null,
      frequency: form.frequency,
      anchor_date: form.anchor_date,
      category_id: isTransfer ? null : form.category_id ? Number(form.category_id) : null,
    };
    const changes: RecurringTransactionUpdateInput = {};
    if (baseline) {
      for (const key of Object.keys(metadata) as (keyof typeof metadata)[]) {
        if (metadata[key] !== baseline[key]) Object.assign(changes, { [key]: metadata[key] });
      }
    }
    if (editMoney) {
      if (!source || (isTransfer && !destination)) {
        setError(t(isTransfer ? "transactions.form.errorSelectDestination" : "transactions.form.errorSelectAccount"));
        return;
      }
      if (isTransfer && form.transfer_account_id === form.account_id) {
        setError(t("transactions.form.errorSameAccount"));
        return;
      }
      if (mismatch) {
        setError(t("recurring.currencyMismatch"));
        await refetchAccounts();
        return;
      }
      if (!form.amount || (crossCurrency && !form.transfer_amount)) {
        setError(t("recurring.reentry"));
        return;
      }
      Object.assign(changes, {
        type: form.type, account_id: source.id,
        category_id: metadata.category_id,
        transfer_account_id: isTransfer ? destination!.id : null,
        amount: form.amount, amount_currency: form.amount_currency,
        // Same-currency destination amount is normalized by the backend.
        transfer_amount: crossCurrency ? form.transfer_amount : null,
        transfer_amount_currency: isTransfer ? form.transfer_amount_currency : null,
      });
    }

    try {
      if (recurring) {
        if (Object.keys(changes).length) await updateRecurring.mutateAsync({ id: recurring.id, input: changes });
      } else {
        await createRecurring.mutateAsync({ ...metadata, ...changes } as RecurringTransactionInput);
      }
      onClose();
    } catch (error) {
      setError(t(recurringErrorKey(error)));
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={recurring ? t("recurring.form.editTitle") : t("recurring.form.newTitle")}
    >
      <form onSubmit={handleSubmit} className="space-y-3">
        {mismatch && <p role="alert" className="text-sm text-danger">{t("recurring.currencyMismatch")}</p>}
        {(recurring || mismatch) && <Button type="button" disabled={isSaving} onClick={reenterMoney}>{t("recurring.editMoney")}</Button>}
        {editMoney && <p>{t("recurring.reentry")}</p>}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="recurring-type">{t("transactions.form.typeLabel")}</Label>
            <Select
              id="recurring-type"
              disabled={!editMoney || isSaving}
              value={form.type}
              onChange={(event) =>
                changePair(form.account_id, "", event.target.value as TransactionType)
              }
            >
              <option value="expense">{t("transactions.form.typeExpense")}</option>
              <option value="income">{t("transactions.form.typeIncome")}</option>
              <option value="transfer">{t("transactions.form.typeTransfer")}</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="recurring-frequency">{t("recurring.form.frequencyLabel")}</Label>
            <Select
              id="recurring-frequency"
              value={form.frequency}
              onChange={(event) => setForm((prev) => ({ ...prev, frequency: event.target.value as RecurringFrequency }))}
            >
              <option value="weekly">{t("recurring.frequency.weekly")}</option>
              <option value="monthly">{t("recurring.frequency.monthly")}</option>
              <option value="yearly">{t("recurring.frequency.yearly")}</option>
            </Select>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="recurring-amount">{t("transactions.form.amountLabel")} ({form.amount_currency || t("financial.unknown")})</Label>
            <Input
              id="recurring-amount"
              type="text"
              inputMode="decimal"
              disabled={!editMoney || isSaving}
              required
              value={form.amount}
              onChange={(event) => setForm((prev) => ({ ...prev, amount: event.target.value }))}
            />
          </div>
          <div>
            <Label htmlFor="recurring-anchor-date">{t("recurring.form.anchorDateLabel")}</Label>
            <Input
              id="recurring-anchor-date"
              type="date"
              required
              value={form.anchor_date}
              onChange={(event) => setForm((prev) => ({ ...prev, anchor_date: event.target.value }))}
            />
          </div>
        </div>

        <div>
          <Label htmlFor="recurring-description">{t("transactions.form.descriptionLabel")}</Label>
          <Input
            id="recurring-description"
            required
            placeholder={t("transactions.form.descriptionPlaceholder")}
            value={form.description}
            onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
          />
        </div>

        <div>
          <Label htmlFor="recurring-account">{t("transactions.form.accountLabel")}</Label>
          <Select
            id="recurring-account"
            disabled={!editMoney || isSaving}
            required
            value={form.account_id}
            onChange={(event) => changePair(event.target.value, form.transfer_account_id)}
          >
            <option value="" disabled>
              {t("transactions.form.selectAccount")}
            </option>
            {accounts?.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} ({account.currency})
              </option>
            ))}
          </Select>
        </div>

        {form.type === "transfer" ? (
          <div>
            <Label htmlFor="recurring-transfer-account">{t("transactions.form.transferAccountLabel")}</Label>
            <Select
              id="recurring-transfer-account"
              disabled={!editMoney || isSaving}
              required
              value={form.transfer_account_id}
              onChange={(event) => changePair(form.account_id, event.target.value)}
            >
              <option value="" disabled>
                {t("transactions.form.selectAccount")}
              </option>
              {accounts
                ?.filter((account) => String(account.id) !== form.account_id)
                .map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} ({account.currency})
                  </option>
                ))}
            </Select>
          </div>
        ) : (
          <div>
            <Label htmlFor="recurring-category">{t("transactions.form.categoryLabel")}</Label>
            <Select
              id="recurring-category"
              value={form.category_id}
              onChange={(event) => setForm((prev) => ({ ...prev, category_id: event.target.value }))}
            >
              <option value="">{t("transactions.form.noCategory")}</option>
              {relevantCategories.map((category) => (
                <option key={category.id} value={category.id}>
                  {translateCategoryName(category.name)}
                </option>
              ))}
            </Select>
          </div>
        )}

        {isTransfer && <div>
          <Label htmlFor="recurring-transfer-amount">{t("recurring.destinationAmount")} ({form.transfer_amount_currency || t("financial.unknown")})</Label>
          <Input id="recurring-transfer-amount" type="text" inputMode="decimal"
            disabled={!editMoney || !crossCurrency || isSaving} required={editMoney && crossCurrency}
            value={editMoney && !crossCurrency ? form.amount : form.transfer_amount}
            onChange={(event) => setForm((prev) => ({ ...prev, transfer_amount: event.target.value }))} />
        </div>}

        <div>
          <Label htmlFor="recurring-merchant">{t("transactions.form.merchantLabel")}</Label>
          <Input
            id="recurring-merchant"
            value={form.merchant}
            onChange={(event) => setForm((prev) => ({ ...prev, merchant: event.target.value }))}
          />
        </div>

        {error && <p role="alert" className="text-sm text-danger">{error}</p>}

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
