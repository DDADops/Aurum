import { ApiError } from "@/api/client";
import type { TranslationKey } from "@/lib/i18n";

export function recurringErrorKey(error: unknown, posting = false): TranslationKey {
  if (error instanceof ApiError) {
    if (["recurring_currency_mismatch", "recurring_currency_revalidation_required",
      "recurring_snapshot_must_match_account", "recurring_monetary_reentry_required"].includes(error.message)) {
      return "recurring.currencyMismatch";
    }
    if (error.status === 409) return "recurring.conflict";
  }
  return posting ? "recurring.postError" : "recurring.form.saveError";
}
