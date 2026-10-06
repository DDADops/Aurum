import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { formatCurrency, pluralizeRu } from "@/lib/format";
import { useTranslation, type Language, type TranslationKey } from "@/lib/i18n";
import type { CapitalRoleSummary } from "@/types";

interface CapitalRoleSummaryCardProps {
  currency: string | undefined;
  roles: CapitalRoleSummary[];
  isLoading: boolean;
}

function assetsCountLabel(count: number, language: Language): string {
  if (language === "ru") return pluralizeRu(count, "актив", "актива", "активов");
  return count === 1 ? "asset" : "assets";
}

export function CapitalRoleSummaryCard({ currency, roles, isLoading }: CapitalRoleSummaryCardProps) {
  const { t, language } = useTranslation();

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("netWorth.capitalByType")}</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="py-10 text-center text-sm text-text-muted">{t("common.loading")}</p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {roles.map((role) => {
              const cashFlow = role.monthly_cash_flow;
              const positive = !cashFlow.startsWith("-");
              return (
                <div key={role.role} className="rounded-lg border border-border p-3.5">
                  <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-text-muted">
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: role.color }} />
                    {t(`netWorth.capitalRole.${role.role}` as TranslationKey)}
                  </p>
                  <p className="mt-1.5 text-xl font-semibold tabular-nums text-text-primary">
                    {formatCurrency(role.total_value, currency)}
                  </p>
                  <p className="mt-1 text-xs text-text-muted">
                    {role.count} {assetsCountLabel(role.count, language)} ·{" "}
                    {t(`netWorth.capitalRoleHint.${role.role}` as TranslationKey)}
                  </p>
                  {!/^[-+]?0(?:\.0+)?$/.test(cashFlow) && (
                    <p
                      className="mt-1.5 text-sm font-medium tabular-nums"
                      style={{ color: positive ? "var(--success)" : "var(--danger)" }}
                    >
                      {positive ? "+" : ""}
                      {formatCurrency(cashFlow, currency)}
                      {t("common.perMonth")}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
