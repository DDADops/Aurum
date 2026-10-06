import { useEffect, useRef, useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Input";
import { useCreateAccount, useUpdateAccount } from "@/hooks/useAccounts";
import { useTranslation, type TranslationKey } from "@/lib/i18n";
import { useAppSettings } from "@/hooks/useSettings";
import { ApiError } from "@/api/client";
import { CURRENCIES, getCurrencyLabel } from "@/lib/currency";
import type { AccountWithBalance, AccountType } from "@/types";

interface AccountFormModalProps {
  open: boolean;
  onClose: () => void;
  account?: AccountWithBalance | null;
}

const ACCOUNT_TYPES: AccountType[] = ["checking", "debit_card", "savings", "credit_card", "cash", "investment", "other"];

const EMPTY_FORM = { name: "", type: "checking" as AccountType, currency: "" };

export function AccountFormModal({ open, onClose, account }: AccountFormModalProps) {
  const { t, language } = useTranslation();
  const { data: settings } = useAppSettings();
  const initialized = useRef(false);
  const currencyLocked = !!account && account.has_monetary_history !== false;
  const createAccount = useCreateAccount();
  const updateAccount = useUpdateAccount();

  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      initialized.current = false;
      return;
    }
    if (initialized.current) return;
    initialized.current = true;
    setForm(account ? { name: account.name, type: account.type, currency: account.currency } : EMPTY_FORM);
    setError(null);
  }, [open, account]);

  const selectedCurrency = currencyLocked && account ? account.currency : form.currency || settings?.currency || "";

  const isSaving = createAccount.isPending || updateAccount.isPending;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    try {
      if (account) {
        await updateAccount.mutateAsync({ id: account.id, input: { ...form, currency: selectedCurrency } });
      } else {
        await createAccount.mutateAsync({ ...form, currency: selectedCurrency });
      }
      onClose();
    } catch (error) {
      setError(t(error instanceof ApiError && error.status === 409 && error.message === "currency_change_requires_empty_history"
        ? "account.currencyHistory" : "account.form.saveError"));
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={account ? t("account.form.editTitle") : t("account.form.newTitle")}>
      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <Label htmlFor="account-name">{t("account.form.nameLabel")}</Label>
          <Input
            id="account-name"
            required
            placeholder={t("account.form.namePlaceholder")}
            value={form.name}
            onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
          />
        </div>

        <div>
          <Label htmlFor="account-type">{t("account.form.typeLabel")}</Label>
          <Select
            id="account-type"
            value={form.type}
            onChange={(event) => setForm((prev) => ({ ...prev, type: event.target.value as AccountType }))}
          >
            {ACCOUNT_TYPES.map((type) => (
              <option key={type} value={type}>
                {t(`account.type.${type}` as TranslationKey)}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <Label htmlFor="account-currency">{t("account.currency")}</Label>
          <Select
            id="account-currency"
            required
            value={selectedCurrency}
            disabled={currencyLocked || isSaving}
            aria-describedby={currencyLocked ? "account-currency-history" : undefined}
            onChange={(event) => setForm((prev) => ({ ...prev, currency: event.target.value }))}
          >
            {!selectedCurrency && <option value="">{t("common.loading")}</option>}
            {CURRENCIES.map(({ code }) => (
              <option key={code} value={code}>{getCurrencyLabel(code, language)}</option>
            ))}
          </Select>
          {currencyLocked && (
            <p id="account-currency-history" className="mt-1 text-xs text-text-muted">
              {t("account.currencyHistory")}
            </p>
          )}
        </div>

        {error && <p className="text-sm text-danger">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" disabled={isSaving || !selectedCurrency}>
            {isSaving ? t("common.saving") : t("common.save")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
