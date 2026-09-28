import { api } from "@/api/client";
import type { AlertsResponse } from "@/types";

export function fetchFinancialAlerts() {
  return api.get<AlertsResponse>("/insights/alerts");
}
