import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Select } from "@/components/ui/Input";
import { useTranslation } from "@/lib/i18n";
import { CURRENCIES, getCurrencyLabel } from "@/lib/currency";
import { useAppSettings, useUpdateAppSettings } from "@/hooks/useSettings";

export function CurrencyCard() {
  const { t, language } = useTranslation();
  const { data: settings, isLoading } = useAppSettings();
  const updateSettings = useUpdateAppSettings();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.currency")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <Select
          aria-label={t("settings.currency")}
          value={settings?.currency ?? ""}
          disabled={isLoading || updateSettings.isPending}
          onChange={(event) => updateSettings.mutate({ currency: event.target.value })}
          className="max-w-xs"
        >
          {CURRENCIES.map((currency) => (
            <option key={currency.code} value={currency.code}>
              {getCurrencyLabel(currency.code, language)}
            </option>
          ))}
        </Select>
        <p className="text-xs text-text-muted">{t("settings.currencyHint")}</p>
      </CardContent>
    </Card>
  );
}
