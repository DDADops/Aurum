import { Label, Select } from "./Input";
import { CURRENCIES, getCurrencyLabel } from "@/lib/currency";
import { useTranslation } from "@/lib/i18n";

export function PlanningCurrencySelect({ id, value, onChange, quote = false, required = true }: {
  id: string; value: string; onChange: (value: string) => void; quote?: boolean; required?: boolean;
}) {
  const { t, language } = useTranslation();
  return <div>
    <Label htmlFor={id}>{t(quote ? "planning.quoteCurrency" : "planning.currency")}</Label>
    <Select id={id} value={value} required={required} onChange={(e) => onChange(e.target.value)}>
      {!value && <option value="">{t(quote ? "planning.unknownQuote" : "financial.unknown")}</option>}
      {CURRENCIES.map(({ code }) => <option key={code} value={code}>{getCurrencyLabel(code, language)}</option>)}
    </Select>
  </div>;
}
