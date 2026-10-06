import { useEffect, useRef, useState } from "react";
import { hasFinancialChanges, transactionPatch, transferDisplayRate, validTransferAmount } from "@/lib/transfer";
import { ApiError } from "@/api/client";
import { moneyToUnits, unitsToMoney, splitRemainder } from "@/lib/money";
import { Plus, X } from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Input";
import { TagInput } from "@/components/transactions/TagInput";
import { useAccounts } from "@/hooks/useAccounts";
import { useCategories } from "@/hooks/useCategories";
import { useCreateTransaction, useUpdateTransaction } from "@/hooks/useTransactions";
import { useTranslation } from "@/lib/i18n";
import { buildHierarchicalCategories, translateCategoryName } from "@/lib/categoryLabels";
import type { Account, Tag, Transaction, TransactionInput, TransactionSplitInput, TransactionType } from "@/types";

interface TransactionFormModalProps {
  open: boolean;
  onClose: () => void;
  transaction?: Transaction | null;
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

const EMPTY_FORM = {
  type: "expense" as TransactionType,
  account_id: "",
  category_id: "",
  transfer_account_id: "",
  transfer_amount: "",
  transfer_pair: "",
  amount: "",
  description: "",
  merchant: "",
  notes: "",
  date: todayIso(),
};

interface SplitRowState {
  key: string;
  category_id: string;
  amount: string;
  note: string;
}

// crypto.randomUUID() only exists in secure contexts (HTTPS/localhost) — on plain
// HTTP (e.g. accessing the app by LAN IP) it's undefined and throws a TypeError.
// This key is only a local React list key, never sent to the backend, so a
// Math.random()-based fallback is fine when the Web Crypto API isn't available.
function generateRowKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `split-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function emptySplitRow(): SplitRowState {
  return { key: generateRowKey(), category_id: "", amount: "", note: "" };
}

export function TransactionFormModal({ open, onClose, transaction }: TransactionFormModalProps) {
  const { t, language } = useTranslation();
  const { data: accounts } = useAccounts();
  const { data: categories } = useCategories();
  const createTransaction = useCreateTransaction();
  const updateTransaction = useUpdateTransaction();

  const initialized = useRef<string | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [tags, setTags] = useState<Tag[]>([]);
  const [splitMode, setSplitMode] = useState(false);
  const [splitRows, setSplitRows] = useState<SplitRowState[]>([emptySplitRow(), emptySplitRow()]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) { initialized.current = null; return; }
    const key = transaction ? String(transaction.id) : "new";
    if (initialized.current === key) return;
    if (!transaction && !accounts) return;
    initialized.current = key;
    if (transaction) {
      const hasSplits = transaction.splits.length > 0;
      // A split's categories always share one parent (see
      // routes/transactions.py's _build_splits) — derive that shared base
      // from whichever split's category is still live. If every split's
      // category was since deleted, there's nothing to derive from; the
      // base field is left blank and the user has to pick one again.
      const baseCategory = hasSplits ? transaction.splits.find((split) => split.category)?.category : null;
      setForm({
        type: transaction.type,
        account_id: String(transaction.account_id),
        category_id: hasSplits
          ? baseCategory
            ? String(baseCategory.parent_id ?? baseCategory.id)
            : ""
          : transaction.category_id
            ? String(transaction.category_id)
            : "",
        transfer_account_id: transaction.transfer_account_id ? String(transaction.transfer_account_id) : "",
        transfer_amount: transaction.transfer_amount ?? "",
        transfer_pair: `${transaction.account_id}:${transaction.account.currency}:${transaction.transfer_account_id}:${transaction.transfer_account?.currency ?? ""}`,
        amount: transaction.amount,
        description: transaction.description,
        merchant: transaction.merchant ?? "",
        notes: transaction.notes ?? "",
        date: transaction.date,
      });
      setTags(transaction.tags);
      setSplitMode(hasSplits);
      setSplitRows(
        hasSplits
          ? transaction.splits.map((split) => ({
              key: String(split.id),
              category_id: split.category_id ? String(split.category_id) : "",
              amount: split.amount,
              note: split.note ?? "",
            }))
          : [emptySplitRow(), emptySplitRow()]
      );
    } else {
      setForm({ ...EMPTY_FORM, account_id: accounts?.[0] ? String(accounts[0].id) : "" });
      setTags([]);
      setSplitMode(false);
      setSplitRows([emptySplitRow(), emptySplitRow()]);
    }
    setError(null);
  }, [open, transaction, accounts]);

  // Existing archived accounts remain selectable when editing their transaction;
  // new choices still come from the active Accounts query.
  const accountOptions: Account[] = [...(accounts ?? [])];
  for (const embedded of [transaction?.account, transaction?.transfer_account]) {
    if (embedded && !accountOptions.some((item) => item.id === embedded.id)) {
      accountOptions.push(embedded);
    }
  }
  const sourceCurrency = accountOptions.find((item) => String(item.id) === form.account_id)?.currency;
  const destinationCurrency = accountOptions.find((item) => String(item.id) === form.transfer_account_id)?.currency;
  const isTransfer = form.type === "transfer";
  const sameCurrency = !!sourceCurrency && sourceCurrency === destinationCurrency;
  const pairKey = `${form.account_id}:${sourceCurrency ?? ""}:${form.transfer_account_id}:${destinationCurrency ?? ""}`;
  useEffect(() => {
    if (form.transfer_pair && form.transfer_pair !== pairKey) {
      setForm((prev) => ({ ...prev, transfer_amount: "", transfer_pair: "" }));
    }
  }, [form.transfer_pair, pairKey]);
  const destinationAmount = sameCurrency ? form.amount : form.transfer_pair === pairKey ? form.transfer_amount : "";
  const unchangedMoney = transaction?.type === "transfer"
    && form.account_id === String(transaction.account_id)
    && form.transfer_account_id === String(transaction.transfer_account_id)
    && sourceCurrency === transaction.account.currency && destinationCurrency === transaction.transfer_account?.currency
    && form.amount === transaction.amount && destinationAmount === transaction.transfer_amount;
  const rate = isTransfer && !sameCurrency
    ? unchangedMoney && transaction.effective_rate !== null ? transaction.effective_rate
      : transferDisplayRate(form.amount, destinationAmount)
    : null;

  function changeAccount(field: "account_id" | "transfer_account_id", value: string) {
    setForm((prev) => ({ ...prev, [field]: value, transfer_amount: "", transfer_pair: "",
      ...(field === "account_id" && value === prev.transfer_account_id ? { transfer_account_id: "" } : {}),
    }));
  }

  const kindCategories = (categories ?? []).filter((category) =>
    form.type === "income" ? category.kind === "income" : category.kind === "expense"
  );
  // Subcategories are listed right under their parent (not scattered by
  // name) so the hierarchy set up on the Categories page reads the same way
  // here.
  const relevantCategories = buildHierarchicalCategories(kindCategories, language);

  const isSaving = createTransaction.isPending || updateTransaction.isPending;

  function updateSplitRow(key: string, patch: Partial<SplitRowState>) {
    setSplitRows((prev) => prev.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  }

  function addSplitRow() {
    setSplitRows((prev) => [...prev, emptySplitRow()]);
  }

  function removeSplitRow(key: string) {
    setSplitRows((prev) => (prev.length <= 2 ? prev : prev.filter((row) => row.key !== key)));
  }

  const isSplitEditingNow = form.type !== "transfer" && splitMode;
  const splitRemainingUnits = splitRemainder(form.amount, splitRows.map((row) => row.amount));

  // The category select becomes the split's "base" category while
  // splitting — restricted to top-level categories — and each split row can
  // only pick that base itself or one of its direct children (see
  // routes/transactions.py's _build_splits: a split's categories always
  // share one parent).
  const topLevelCategories = relevantCategories.filter((category) => !category.indented);
  const baseChildCategories = kindCategories.filter((category) => category.parent_id === Number(form.category_id));
  const categorySelectOptions = splitMode ? topLevelCategories : relevantCategories;

  function toggleSplitMode() {
    setSplitMode((prev) => {
      const next = !prev;
      if (next) {
        const current = relevantCategories.find((category) => String(category.id) === form.category_id);
        if (current?.parent_id) {
          setForm((f) => ({ ...f, category_id: String(current.parent_id) }));
        }
        setSplitRows([emptySplitRow(), emptySplitRow()]);
      }
      return next;
    });
  }

  function handleBaseCategoryChange(value: string) {
    setForm((prev) => ({ ...prev, category_id: value }));
    if (splitMode) setSplitRows([emptySplitRow(), emptySplitRow()]);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (!validTransferAmount(form.amount)) {
      setError(t("transactions.form.invalidAmount"));
      return;
    }
    if (!form.account_id) {
      setError(t("transactions.form.errorSelectAccount"));
      return;
    }
    if (form.type === "transfer" && !form.transfer_account_id && !transaction) {
      setError(t("transactions.form.errorSelectDestination"));
      return;
    }
    if (form.type === "transfer" && form.transfer_account_id === form.account_id) {
      setError(t("transactions.form.errorSameAccount"));
      return;
    }

    // Undefined -> leave the transaction's existing splits untouched on
    // update, and create a normal single-category transaction. [] -> clears
    // splits that used to be there (the user turned split mode off, or
    // switched the transaction to a transfer, which can't carry splits).
    let splits: TransactionSplitInput[] | undefined;
    if (isSplitEditingNow) {
      if (!form.category_id) {
        setError(t("transactions.form.errorSplitNoBaseCategory"));
        return;
      }
      const filledRows = splitRows.filter((row) => row.category_id || row.amount);
      if (filledRows.length < 2) {
        setError(t("transactions.form.errorSplitMinRows"));
        return;
      }
      if (filledRows.some((row) => !row.category_id || !row.amount || (moneyToUnits(row.amount) ?? 0n) <= 0n)) {
        setError(t("transactions.form.errorSplitIncomplete"));
        return;
      }
      if (splitRemainder(form.amount, filledRows.map((row) => row.amount)) !== 0n) {
        setError(t("transactions.form.errorSplitMismatch"));
        return;
      }
      splits = filledRows.map((row) => ({
        category_id: Number(row.category_id),
        amount: row.amount,
        note: row.note || null,
      }));
    } else if (transaction && transaction.splits.length > 0) {
      splits = [];
    }

    const payload: TransactionInput = {
      type: form.type,
      account_id: Number(form.account_id),
      category_id:
        form.type === "transfer" || (splits && splits.length > 0)
          ? null
          : form.category_id
            ? Number(form.category_id)
            : null,
      transfer_account_id: form.type === "transfer" && form.transfer_account_id ? Number(form.transfer_account_id) : null,
      amount: form.amount,
      ...(isTransfer ? { transfer_amount: destinationAmount || null } : {}),
      description: form.description,
      merchant: form.merchant || null,
      notes: form.notes || null,
      date: form.date,
      tag_ids: tags.map((tag) => tag.id),
      splits,
    };

    const patch = transaction ? transactionPatch(payload, transaction) : null;
    // An unresolved metadata edit must not accidentally reconcile a same-currency
    // legacy row either. Financial edits still submit the exact synchronized side.
    if (patch && transaction?.type === "transfer" && transaction.transfer_amount === null && sameCurrency) {
      delete patch.transfer_amount;
      if (hasFinancialChanges(patch)) patch.transfer_amount = form.amount;
    }
    const financial = !patch || hasFinancialChanges(patch);
    if (isTransfer && financial) {
      if (!form.transfer_account_id || !sourceCurrency || !destinationCurrency) {
        setError(t("transactions.form.errorSelectDestination"));
        return;
      }
      if (!validTransferAmount(destinationAmount)) {
        setError(t(destinationAmount ? "transactions.form.invalidAmount" : "transactions.form.reconciliationRequired"));
        return;
      }
      // A changed pair always needs an explicitly accepted credit, even if its
      // numeric value happens to match the previously stored credit.
      if (patch && (patch.account_id !== undefined || patch.transfer_account_id !== undefined || patch.type === "transfer"
        || (sameCurrency && patch.amount !== undefined))) patch.transfer_amount = destinationAmount;
    }

    try {
      if (transaction && patch) {
        if (Object.keys(patch).length > 0) await updateTransaction.mutateAsync({ id: transaction.id, input: patch });
      } else {
        await createTransaction.mutateAsync(payload);
      }
      onClose();
    } catch (error) {
      setError(t(error instanceof ApiError && [400, 409, 422].includes(error.status)
        ? "transactions.form.rejected" : "transactions.form.saveError"));
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={transaction ? t("transactions.form.editTitle") : t("transactions.form.newTitle")}
    >
      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <Label htmlFor="type">{t("transactions.form.typeLabel")}</Label>
          <Select
            id="type"
            value={form.type}
            onChange={(event) => {
              const nextType = event.target.value as TransactionType;
              setForm((prev) => ({ ...prev, type: nextType, category_id: "", transfer_account_id: "", transfer_amount: "", transfer_pair: "" }));
              if (nextType === "transfer") setSplitMode(false);
            }}
          >
            <option value="expense">{t("transactions.form.typeExpense")}</option>
            <option value="income">{t("transactions.form.typeIncome")}</option>
            <option value="transfer">{t("transactions.form.typeTransfer")}</option>
          </Select>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="amount">{t(isTransfer ? "transactions.form.amountSent" : "transactions.form.amountLabel")} {sourceCurrency && `(${sourceCurrency})`}</Label>
            <Input
              id="amount"
              type="text"
              inputMode="decimal"
              required
              value={form.amount}
              onChange={(event) => setForm((prev) => ({ ...prev, amount: event.target.value }))}
            />
          </div>
          <div>
            <Label htmlFor="date">{t("transactions.form.dateLabel")}</Label>
            <Input
              id="date"
              type="date"
              required
              value={form.date}
              onChange={(event) => setForm((prev) => ({ ...prev, date: event.target.value }))}
            />
          </div>
        </div>

        {isTransfer && !sameCurrency && (
          <div>
            <Label htmlFor="transfer-amount">{t("transactions.form.amountReceived")} {destinationCurrency && `(${destinationCurrency})`}</Label>
            <Input id="transfer-amount" type="text" inputMode="decimal" value={destinationAmount}
              aria-describedby={!destinationAmount ? "transfer-reconciliation" : undefined}
              onChange={(event) => setForm((prev) => ({ ...prev, transfer_amount: event.target.value, transfer_pair: pairKey }))} />
            {!destinationAmount && <p id="transfer-reconciliation" role="status" className="mt-1 text-xs text-text-muted">{t("transactions.needsReconciliation")}</p>}
          </div>
        )}
        {rate && <p className="text-xs text-text-muted" aria-live="polite">
          {t("transactions.form.transactionRate")}: 1 {sourceCurrency} ≈ {rate} {destinationCurrency}
        </p>}

        <div>
          <Label htmlFor="description">{t("transactions.form.descriptionLabel")}</Label>
          <Input
            id="description"
            required
            placeholder={t("transactions.form.descriptionPlaceholder")}
            value={form.description}
            onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
          />
        </div>

        <div>
          <Label htmlFor="account">{t(isTransfer ? "transactions.form.sourceAccount" : "transactions.form.accountLabel")}</Label>
          <Select
            id="account"
            required
            value={form.account_id}
            onChange={(event) => changeAccount("account_id", event.target.value)}
          >
            <option value="" disabled>
              {t("transactions.form.selectAccount")}
            </option>
            {accountOptions.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name} · {account.currency}
              </option>
            ))}
          </Select>
        </div>

        {form.type === "transfer" ? (
          <div>
            <Label htmlFor="transfer_account">{t("transactions.form.destinationAccount")}</Label>
            <Select
              id="transfer_account"
              required
              value={form.transfer_account_id}
              onChange={(event) => changeAccount("transfer_account_id", event.target.value)}
            >
              <option value="" disabled>
                {t("transactions.form.selectAccount")}
              </option>
              {accountOptions
                .filter((account) => String(account.id) !== form.account_id)
                .map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} · {account.currency}
                  </option>
                ))}
            </Select>
          </div>
        ) : (
          <div>
            <div className="flex items-center justify-between">
              <Label htmlFor="category">{t("transactions.form.categoryLabel")}</Label>
              <button
                type="button"
                className="mb-1 text-xs text-series-1 hover:underline"
                onClick={toggleSplitMode}
              >
                {splitMode ? t("transactions.form.splitToggleOff") : t("transactions.form.splitToggle")}
              </button>
            </div>

            <Select
              id="category"
              value={form.category_id}
              onChange={(event) => handleBaseCategoryChange(event.target.value)}
            >
              <option value="">{t("transactions.form.noCategory")}</option>
              {categorySelectOptions.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.indented ? `    ↳ ` : ""}
                  {translateCategoryName(category.name)}
                </option>
              ))}
            </Select>

            {splitMode && (
              <div className="mt-2 space-y-2">
                {!form.category_id ? (
                  <p className="text-xs text-text-muted">{t("transactions.form.splitHint")}</p>
                ) : baseChildCategories.length === 0 ? (
                  <p className="text-xs text-text-muted">{t("transactions.form.splitNoChildren")}</p>
                ) : (
                  <p className="text-xs text-text-muted">{t("transactions.form.splitHint")}</p>
                )}
                {splitRows.map((row) => (
                  <div key={row.key} className="space-y-1.5 rounded-lg border border-border bg-surface-1 p-2">
                    <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2">
                      <Select
                        aria-label={t("transactions.form.splitCategoryPlaceholder")}
                        className="sm:flex-1"
                        value={row.category_id}
                        disabled={!form.category_id}
                        onChange={(event) => updateSplitRow(row.key, { category_id: event.target.value })}
                      >
                        <option value="" disabled>
                          {t("transactions.form.splitCategoryPlaceholder")}
                        </option>
                        {form.category_id && (
                          <option value={form.category_id}>{t("transactions.form.splitDirectOption")}</option>
                        )}
                        {baseChildCategories.map((category) => (
                          <option key={category.id} value={category.id}>
                            {translateCategoryName(category.name)}
                          </option>
                        ))}
                      </Select>
                      <div className="flex items-center gap-1.5">
                        <Input
                          type="number"
                          step="0.000001"
                          min="0.000001"
                          className="w-24"
                          placeholder={t("transactions.form.amountLabel")}
                          value={row.amount}
                          onChange={(event) => updateSplitRow(row.key, { amount: event.target.value })}
                        />
                        <button
                          type="button"
                          aria-label={t("transactions.form.splitRemoveRow")}
                          onClick={() => removeSplitRow(row.key)}
                          disabled={splitRows.length <= 2}
                          className="shrink-0 rounded-md p-1.5 text-text-muted hover:bg-surface-2 hover:text-danger disabled:opacity-30"
                        >
                          <X size={15} />
                        </button>
                      </div>
                    </div>
                    <Input
                      className="text-xs"
                      placeholder={t("transactions.form.splitNotePlaceholder")}
                      value={row.note}
                      onChange={(event) => updateSplitRow(row.key, { note: event.target.value })}
                    />
                  </div>
                ))}

                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    onClick={addSplitRow}
                    className="flex items-center gap-1 rounded-md py-1 text-xs text-series-1 hover:underline"
                  >
                    <Plus size={14} />
                    {t("transactions.form.splitAddRow")}
                  </button>
                  <p className={`text-xs ${splitRemainingUnits === 0n ? "text-success" : "text-text-muted"}`}>
                    {splitRemainingUnits === null ? t("financial.unknown") : splitRemainingUnits > 0n
                      ? t("transactions.form.splitRemainingLabel", {
                          amount: unitsToMoney(splitRemainingUnits),
                        })
                      : splitRemainingUnits < 0n
                        ? t("transactions.form.splitOverAllocatedLabel", {
                            amount: unitsToMoney(-splitRemainingUnits),
                          })
                        : t("transactions.form.splitFullyAllocatedLabel")}
                  </p>
                </div>
              </div>
            )}
          </div>
        )}

        <div>
          <Label htmlFor="merchant">{t("transactions.form.merchantLabel")}</Label>
          <Input
            id="merchant"
            value={form.merchant}
            onChange={(event) => setForm((prev) => ({ ...prev, merchant: event.target.value }))}
          />
        </div>

        <div>
          <Label htmlFor="notes">{t("transactions.form.notesLabel")}</Label>
          {/* Matches the server's max_length on notes — without it an overlong
              note only fails on save, as an untranslated 422. */}
          <Input
            id="notes"
            maxLength={2000}
            value={form.notes}
            onChange={(event) => setForm((prev) => ({ ...prev, notes: event.target.value }))}
          />
        </div>

        <div>
          <Label htmlFor="transaction-tags">{t("transactions.form.tagsLabel")}</Label>
          <TagInput value={tags} onChange={setTags} />
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
