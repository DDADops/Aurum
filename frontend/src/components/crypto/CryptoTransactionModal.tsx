import { PlanningCurrencySelect } from "@/components/ui/PlanningCurrencySelect";
import { useAppSettings } from "@/hooks/useSettings";
import { validPositiveDecimal } from "@/lib/planningMoney";
import { type FormEvent, useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input, Label } from "@/components/ui/Input";
import { useAddCryptoTransaction, useUpdateCryptoTransaction } from "@/hooks/useCrypto";
import { trimTrailingZeros } from "@/lib/format";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { CryptoHolding, CryptoTransaction, CryptoTransactionType } from "@/types";

interface CryptoTransactionModalProps {
  open: boolean;
  onClose: () => void;
  holding: CryptoHolding | null;
  // Present -> editing that transaction (pre-filled, PATCHes it). Absent ->
  // recording a new buy/sell against `holding` (the "+" action).
  transaction?: CryptoTransaction | null;
}

/** Buy more of, or sell some of, a coin already being tracked — the "+"
 * action on the holdings table — or edit/view an existing entry from its
 * trade history. Never calls CoinGecko either way (see
 * services/crypto_service.py's add_transaction/update_transaction): value
 * is recomputed from the last cached price. */
export function CryptoTransactionModal({ open, onClose, holding, transaction = null }: CryptoTransactionModalProps) {
  const { t } = useTranslation();
  const addTransaction = useAddCryptoTransaction();
  const updateTransaction = useUpdateCryptoTransaction();
  const isEditing = transaction !== null;

  const [type, setType] = useState<CryptoTransactionType>("buy");
  const [quantity, setQuantity] = useState("");
  const [pricePerUnit, setPricePerUnit] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [note, setNote] = useState("");
  const { data: settings } = useAppSettings();
  const [currency, setCurrency] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCurrency(transaction ? transaction.quote_currency ?? "" : settings?.currency ?? "");
    if (transaction) {
      setType(transaction.type);
      // The API returns these at full Numeric(38,18) scale (e.g.
      // "3.000000000000000000") — trimmed here so the field shows what was
      // actually entered, not a wall of padding zeros.
      setQuantity(trimTrailingZeros(transaction.quantity));
      setPricePerUnit(trimTrailingZeros(transaction.price_per_unit));
      setDate(transaction.date);
      setNote(transaction.note ?? "");
    } else {
      setType("buy");
      setQuantity("");
      setPricePerUnit("");
      setDate(new Date().toISOString().slice(0, 10));
      setNote("");
    }
    setError(null);
  }, [open, holding, transaction]);

  useEffect(() => { if (open && !transaction && !currency && settings?.currency) setCurrency(settings.currency); }, [open, transaction, currency, settings?.currency]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!holding) return;
    setError(null);
    const financialChanged = !transaction || type !== transaction.type || quantity !== trimTrailingZeros(transaction.quantity) || pricePerUnit !== trimTrailingZeros(transaction.price_per_unit) || date !== transaction.date || currency !== (transaction.quote_currency ?? "");
    if (financialChanged && (!currency || !validPositiveDecimal(quantity, 38, 18) || !validPositiveDecimal(pricePerUnit, 38, 18))) {
      setError(t(!currency ? "planning.unknownQuote" : "planning.invalidCrypto")); return;
    }
    const input = transaction && !financialChanged
      ? { note: note || null }
      : { type, quantity, price_per_unit: pricePerUnit, quote_currency: currency, date, note: note || null };
    try {
      if (transaction) {
        await updateTransaction.mutateAsync({ transactionId: transaction.id, input });
      } else {
        await addTransaction.mutateAsync({ assetId: holding.asset_id, input: { type, quantity, price_per_unit: pricePerUnit, quote_currency: currency, date, note: note || null } });
      }
      onClose();
    } catch (err) {
      setError(
        err instanceof ApiError && err.message.includes("Cannot sell more") ? t("crypto.form.errorSellTooMuch") : t("crypto.form.saveError")
      );
    }
  }

  if (!holding) return null;

  const isPending = addTransaction.isPending || updateTransaction.isPending;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={isEditing ? t("crypto.form.editTradeTitle", { name: holding.name }) : t("crypto.form.tradeTitle", { name: holding.name })}
    >
      <form onSubmit={handleSubmit} className="space-y-3">
        {transaction?.quote_currency === null && <p role="status" className="text-sm text-text-muted">{t("planning.reconcileQuote")}</p>}
        <PlanningCurrencySelect id="planning-currency" value={currency} onChange={setCurrency} quote required={!transaction || transaction.quote_currency !== null} />
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => setType("buy")}
            className={cn(
              "rounded-lg border px-3 py-2 text-sm font-medium transition-colors",
              type === "buy" ? "border-success bg-success/10 text-success" : "border-border text-text-secondary"
            )}
          >
            {t("crypto.form.buy")}
          </button>
          <button
            type="button"
            onClick={() => setType("sell")}
            className={cn(
              "rounded-lg border px-3 py-2 text-sm font-medium transition-colors",
              type === "sell" ? "border-danger bg-danger/10 text-danger" : "border-border text-text-secondary"
            )}
          >
            {t("crypto.form.sell")}
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="tx-quantity">{t("crypto.form.quantityLabel")}</Label>
            <Input
              id="tx-quantity"
              type="text" inputMode="decimal"
              step="any"
              min="0"
              required
              autoFocus
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="tx-price">{t("crypto.form.pricePerUnitLabel")}</Label>
            <Input
              id="tx-price"
              type="text" inputMode="decimal"
              step="any"
              min="0"
              required
              value={pricePerUnit}
              onChange={(event) => setPricePerUnit(event.target.value)}
            />
          </div>
        </div>

        <div>
          <Label htmlFor="tx-date">{t("crypto.form.dateLabel")}</Label>
          <Input id="tx-date" type="date" required value={date} onChange={(event) => setDate(event.target.value)} />
        </div>

        <div>
          <Label htmlFor="tx-note">{t("crypto.form.noteLabel")}</Label>
          <Input id="tx-note" value={note} onChange={(event) => setNote(event.target.value)} />
        </div>

        {error && <p className="text-sm text-danger">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" disabled={isPending}>
            {isPending ? t("common.saving") : t("common.save")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
