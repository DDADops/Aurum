import { useEffect, useRef, useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Input";
import { useAddAssetValuation, useAssets, useCreateAsset, useUpdateAsset } from "@/hooks/useAssets";
import { useTranslation, type TranslationKey } from "@/lib/i18n";
import { useAppSettings } from "@/hooks/useSettings";
import { ApiError } from "@/api/client";
import { CURRENCIES, getCurrencyLabel } from "@/lib/currency";
import type { Asset, AssetClass, CapitalRole, RiskLevel } from "@/types";

interface AssetFormModalProps {
  open: boolean;
  onClose: () => void;
  asset?: Asset | null;
}

const ASSET_CLASSES: AssetClass[] = ["investments", "crypto", "real_estate", "vehicles", "precious_metals", "other"];
const CAPITAL_ROLES: CapitalRole[] = ["income", "neutral", "drain"];
const RISK_LEVELS: RiskLevel[] = ["low", "medium", "high"];

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

const EMPTY_FORM = {
  currency: "",
  name: "",
  asset_class: "investments" as AssetClass,
  value: "",
  as_of_date: todayIso(),
  notes: "",
  capital_role: "neutral" as CapitalRole,
  monthly_cash_flow: "",
  risk_level: "medium" as RiskLevel,
};

export function AssetFormModal({ open, onClose, asset }: AssetFormModalProps) {
  const { t, language } = useTranslation();
  const { data: settings } = useAppSettings();
  const { data: assets, refetch: refetchAssets } = useAssets();
  const authoritativeAsset = assets?.find((row) => row.id === asset?.id) ?? asset;
  const initialized = useRef(false);
  const initialAsset = useRef<Asset | null>(null);
  const [currencySelected, setCurrencySelected] = useState(false);
  const [cashFlowResolved, setCashFlowResolved] = useState(false);
  const createAsset = useCreateAsset();
  const updateAsset = useUpdateAsset();
  const addValuation = useAddAssetValuation();

  const [form, setForm] = useState(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      initialized.current = false;
      return;
    }
    if (initialized.current || (!asset && !settings)) return;
    initialized.current = true;
    setCashFlowResolved(false);
    setCurrencySelected(false);
    initialAsset.current = asset ? { ...asset } : null;
    if (asset) {
      setForm({
        currency: asset.currency,
        name: asset.name,
        asset_class: asset.asset_class,
        value: "",
        as_of_date: todayIso(),
        notes: asset.notes ?? "",
        capital_role: asset.capital_role,
        monthly_cash_flow: asset.monthly_cash_flow ?? "",
        risk_level: asset.risk_level,
      });
    } else {
      setForm({ ...EMPTY_FORM, as_of_date: todayIso(), currency: settings!.currency });
    }
    setError(null);
  }, [open, asset, settings]);

  const baseline = initialAsset.current;
  const currencyChanged = currencySelected && !!baseline && form.currency !== baseline.currency;
  const cashFlowChanged = !!baseline && form.monthly_cash_flow !== (baseline.monthly_cash_flow ?? "");
  const staleCurrency = !!baseline && !!authoritativeAsset && authoritativeAsset.currency !== baseline.currency;
  const cashFlowNeedsResolution = currencyChanged && baseline?.monthly_cash_flow !== null;

  const isSaving = createAsset.isPending || updateAsset.isPending || addValuation.isPending;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (staleCurrency && (currencyChanged || cashFlowChanged || form.value !== "")) {
      setError(t("netWorth.form.staleCurrency"));
      await refetchAssets();
      return;
    }
    if (cashFlowNeedsResolution && !cashFlowResolved) {
      setError(t("netWorth.form.cashFlowReplacement"));
      return;
    }
    let metadataSaved = false;
    try {
      if (asset) {
        await updateAsset.mutateAsync({
          id: asset.id,
          input: {
            ...(currencyChanged ? { currency: form.currency } : {}),
            name: form.name,
            asset_class: form.asset_class,
            notes: form.notes || null,
            capital_role: form.capital_role,
            ...(currencyChanged || cashFlowChanged
              ? { monthly_cash_flow: form.monthly_cash_flow || null } : {}),
            risk_level: form.risk_level,
          },
        });
        metadataSaved = true;
        if (form.value !== "") {
          await addValuation.mutateAsync({ id: asset.id, input: { value: form.value, as_of_date: form.as_of_date, expected_currency: form.currency } });
        }
      } else {
        await createAsset.mutateAsync({
          currency: form.currency,
          name: form.name,
          asset_class: form.asset_class,
          value: form.value,
          as_of_date: form.as_of_date,
          notes: form.notes || null,
          capital_role: form.capital_role,
          monthly_cash_flow: form.monthly_cash_flow || null,
          risk_level: form.risk_level,
        });
      }
      if (staleCurrency) {
        setError(t("netWorth.form.staleCurrency"));
        await refetchAssets();
        return;
      }
      onClose();
    } catch (error) {
      const keys: Record<string, TranslationKey> = {
        asset_currency_mismatch: "netWorth.form.currencyMismatch",
        currency_change_requires_empty_history: "netWorth.form.currencyHistory",
        currency_change_requires_cash_flow_replacement: "netWorth.form.cashFlowReplacement",
        valuation_provenance_conflict: "netWorth.form.provenanceConflict",
      };
      const key = error instanceof ApiError ? keys[error.message] : undefined;
      setError([metadataSaved ? t("netWorth.form.partialSave") : "", t(key ?? "netWorth.form.saveError")].filter(Boolean).join(" "));
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={asset ? t("netWorth.form.editTitle") : t("netWorth.form.newTitle")}>
      <form onSubmit={handleSubmit}>
        <fieldset disabled={isSaving} className="space-y-3">
          <div>
            <Label htmlFor="asset-name">{t("netWorth.form.nameLabel")}</Label>
            <Input
              id="asset-name"
              required
              placeholder={t("netWorth.form.namePlaceholder")}
              value={form.name}
              onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
            />
          </div>

          <div>
            <Label htmlFor="asset-class">{t("netWorth.form.classLabel")}</Label>
            <Select
              id="asset-class"
              value={form.asset_class}
              onChange={(event) => setForm((prev) => ({ ...prev, asset_class: event.target.value as AssetClass }))}
            >
              {ASSET_CLASSES.map((value) => (
                <option key={value} value={value}>
                  {t(`netWorth.assetClass.${value}` as TranslationKey)}
                </option>
              ))}
            </Select>
          </div>

          <div>
            <Label htmlFor="asset-currency">{t("netWorth.form.currency")}</Label>
            <Select id="asset-currency" required value={form.currency}
              disabled={!!authoritativeAsset && authoritativeAsset.has_monetary_history !== false}
              onChange={(event) => {
                setCurrencySelected(true);
                setForm((prev) => ({
                  ...prev, currency: event.target.value, value: "",
                  monthly_cash_flow: event.target.value === baseline?.currency ? baseline.monthly_cash_flow ?? "" : "",
                }));
                setCashFlowResolved(false);
              }}>
              {!form.currency && <option value="">{t("common.loading")}</option>}
              {CURRENCIES.map(({ code }) => <option key={code} value={code}>{getCurrencyLabel(code, language)}</option>)}
            </Select>
            {authoritativeAsset?.has_monetary_history && <p>{t("netWorth.form.currencyHistory")}</p>}
          </div>
          {asset && <p data-testid="asset-valuation-provenance">
            {t("netWorth.form.currentValueLabel")}: {authoritativeAsset?.current_value} {authoritativeAsset?.valuation_currency_confirmed && authoritativeAsset.valuation_currency
              ? authoritativeAsset.valuation_currency : t("netWorth.form.unknownValuation")}
          </p>}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="asset-value">
                {asset ? t("netWorth.form.newValuation") : t("netWorth.form.valueLabel")} ({form.currency})
              </Label>
              <Input
                id="asset-value"
                type="text"
                inputMode="decimal"
                required={!asset}
                value={form.value}
                onChange={(event) => setForm((prev) => ({ ...prev, value: event.target.value }))}
              />
            </div>
            <div>
              <Label htmlFor="asset-date">{t("netWorth.form.dateLabel")}</Label>
              <Input
                id="asset-date"
                type="date"
                required
                value={form.as_of_date}
                onChange={(event) => setForm((prev) => ({ ...prev, as_of_date: event.target.value }))}
              />
            </div>
          </div>

          <div>
            <Label htmlFor="asset-role">{t("netWorth.form.roleLabel")}</Label>
            <Select
              id="asset-role"
              value={form.capital_role}
              onChange={(event) => setForm((prev) => ({ ...prev, capital_role: event.target.value as CapitalRole }))}
            >
              {CAPITAL_ROLES.map((value) => (
                <option key={value} value={value}>
                  {t(`netWorth.capitalRole.${value}` as TranslationKey)}
                </option>
              ))}
            </Select>
            <p className="mt-1 text-xs text-text-muted">
              {t(`netWorth.capitalRoleFormHint.${form.capital_role}` as TranslationKey)}
            </p>
          </div>

          <div>
            <Label htmlFor="asset-risk">{t("netWorth.form.riskLevelLabel")}</Label>
            <Select
              id="asset-risk"
              value={form.risk_level}
              onChange={(event) => setForm((prev) => ({ ...prev, risk_level: event.target.value as RiskLevel }))}
            >
              {RISK_LEVELS.map((value) => (
                <option key={value} value={value}>
                  {t(`netWorth.riskLevel.${value}` as TranslationKey)}
                </option>
              ))}
            </Select>
            <p className="mt-1 text-xs text-text-muted">
              {t(`netWorth.riskLevelFormHint.${form.risk_level}` as TranslationKey)}
            </p>
          </div>

          <div>
            <Label htmlFor="asset-cash-flow">{t("netWorth.form.cashFlowLabel")} ({form.currency})</Label>
            <Input
              id="asset-cash-flow"
              type="text"
              inputMode="decimal"
              placeholder={t("netWorth.form.cashFlowPlaceholder")}
              value={form.monthly_cash_flow}
              onChange={(event) => {
                setForm((prev) => ({ ...prev, monthly_cash_flow: event.target.value }));
                setCashFlowResolved(true);
              }}
            />
            <p className="mt-1 text-xs text-text-muted">{t("netWorth.form.cashFlowHint")}</p>
            {cashFlowNeedsResolution && <>
              <p>{t("netWorth.form.cashFlowReplacement")}</p>
              <Button type="button" onClick={() => {
                setForm((prev) => ({ ...prev, monthly_cash_flow: "" }));
                setCashFlowResolved(true);
              }}>{t("netWorth.form.clearCashFlow")}</Button>
            </>}
          </div>

          <div>
            <Label htmlFor="asset-notes">{t("netWorth.form.notesLabel")}</Label>
            <Input
              id="asset-notes"
              maxLength={2000}
              value={form.notes}
              onChange={(event) => setForm((prev) => ({ ...prev, notes: event.target.value }))}
            />
          </div>

          {error && <p className="text-sm text-danger">{error}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={isSaving || !form.currency}>
              {isSaving ? t("common.saving") : t("common.save")}
            </Button>
          </div>
        </fieldset>
      </form>
    </Dialog>
  );
}
