import { useEffect, useState } from "react";
import { moneyToUnits, unitsToMoney } from "@/lib/money";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input, Label } from "@/components/ui/Input";
import { useTranslation } from "@/lib/i18n";
import { useAppSettings, useUpdateAppSettings } from "@/hooks/useSettings";

const MIN_MONTHS = 1;
const MAX_MONTHS = 24;
const MIN_PERCENT = 1;
const MAX_PERCENT = 100;
const MIN_AMOUNT = 0;
const MIN_DAYS = 1;
const MAX_DAYS = 365;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function AlertThresholdsCard() {
  const { t } = useTranslation();
  const { data: settings } = useAppSettings();
  const updateSettings = useUpdateAppSettings();

  const [cashFlowMonths, setCashFlowMonths] = useState("");
  const [netWorthMonths, setNetWorthMonths] = useState("");
  const [riskyPercent, setRiskyPercent] = useState("");
  const [idleCashAmount, setIdleCashAmount] = useState("");
  const [idleCashDays, setIdleCashDays] = useState("");

  useEffect(() => {
    if (!settings) return;
    setCashFlowMonths(String(settings.negative_cash_flow_threshold_months));
    setNetWorthMonths(String(settings.net_worth_decline_threshold_months));
    setRiskyPercent(String(settings.risky_allocation_threshold_percent));
    setIdleCashAmount(settings.idle_cash_threshold_amount);
    setIdleCashDays(String(settings.idle_cash_threshold_days));
  }, [settings]);

  function commitCashFlowMonths() {
    const value = clamp(Number(cashFlowMonths) || MIN_MONTHS, MIN_MONTHS, MAX_MONTHS);
    setCashFlowMonths(String(value));
    if (settings && value !== settings.negative_cash_flow_threshold_months) {
      updateSettings.mutate({ negative_cash_flow_threshold_months: value });
    }
  }

  function commitNetWorthMonths() {
    const value = clamp(Number(netWorthMonths) || MIN_MONTHS, MIN_MONTHS, MAX_MONTHS);
    setNetWorthMonths(String(value));
    if (settings && value !== settings.net_worth_decline_threshold_months) {
      updateSettings.mutate({ net_worth_decline_threshold_months: value });
    }
  }

  function commitRiskyPercent() {
    const value = clamp(Number(riskyPercent) || MIN_PERCENT, MIN_PERCENT, MAX_PERCENT);
    setRiskyPercent(String(value));
    if (settings && value !== settings.risky_allocation_threshold_percent) {
      updateSettings.mutate({ risky_allocation_threshold_percent: value });
    }
  }

  function commitIdleCashAmount() {
    const units = moneyToUnits(idleCashAmount);
    if (units === null || units < 0n) return;
    const value = unitsToMoney(units);
    setIdleCashAmount(value);
    if (settings && value !== settings.idle_cash_threshold_amount) {
      updateSettings.mutate({ idle_cash_threshold_amount: value });
    }
  }

  function commitIdleCashDays() {
    const value = clamp(Number(idleCashDays) || MIN_DAYS, MIN_DAYS, MAX_DAYS);
    setIdleCashDays(String(value));
    if (settings && value !== settings.idle_cash_threshold_days) {
      updateSettings.mutate({ idle_cash_threshold_days: value });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.alertsTitle")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <Label htmlFor="cash-flow-threshold">{t("settings.negativeCashFlowThresholdLabel")}</Label>
            <Input
              id="cash-flow-threshold"
              type="number"
              min={MIN_MONTHS}
              max={MAX_MONTHS}
              step={1}
              value={cashFlowMonths}
              onChange={(event) => setCashFlowMonths(event.target.value)}
              onBlur={commitCashFlowMonths}
            />
          </div>
          <div>
            <Label htmlFor="net-worth-threshold">{t("settings.netWorthDeclineThresholdLabel")}</Label>
            <Input
              id="net-worth-threshold"
              type="number"
              min={MIN_MONTHS}
              max={MAX_MONTHS}
              step={1}
              value={netWorthMonths}
              onChange={(event) => setNetWorthMonths(event.target.value)}
              onBlur={commitNetWorthMonths}
            />
          </div>
          <div>
            <Label htmlFor="risky-allocation-threshold">{t("settings.riskyAllocationThresholdLabel")}</Label>
            <Input
              id="risky-allocation-threshold"
              type="number"
              min={MIN_PERCENT}
              max={MAX_PERCENT}
              step={1}
              value={riskyPercent}
              onChange={(event) => setRiskyPercent(event.target.value)}
              onBlur={commitRiskyPercent}
            />
          </div>
          <div>
            <Label htmlFor="idle-cash-amount-threshold">{t("settings.idleCashThresholdAmountLabel")}</Label>
            <Input
              id="idle-cash-amount-threshold"
              type="number"
              min={MIN_AMOUNT}
              step="0.000001"
              value={idleCashAmount}
              onChange={(event) => setIdleCashAmount(event.target.value)}
              onBlur={commitIdleCashAmount}
            />
          </div>
          <div>
            <Label htmlFor="idle-cash-days-threshold">{t("settings.idleCashThresholdDaysLabel")}</Label>
            <Input
              id="idle-cash-days-threshold"
              type="number"
              min={MIN_DAYS}
              max={MAX_DAYS}
              step={1}
              value={idleCashDays}
              onChange={(event) => setIdleCashDays(event.target.value)}
              onBlur={commitIdleCashDays}
            />
          </div>
        </div>
        <p className="text-xs text-text-muted">{t("settings.alertsHint")}</p>
      </CardContent>
    </Card>
  );
}
