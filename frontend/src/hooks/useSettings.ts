import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchSettings, updateSettings } from "@/api/settings";
import { setCurrency } from "@/lib/i18n";
import type { AppSettings } from "@/types";

export function useAppSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: fetchSettings });
}

export function useUpdateAppSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Partial<AppSettings>) => updateSettings(input),
    onSuccess: (data) => {
      const previous = queryClient.getQueryData<AppSettings>(["settings"]);
      queryClient.setQueryData(["settings"], data);
      if (previous?.currency !== data.currency) {
        for (const key of [
          "dashboard-summary", "net-worth-summary", "cash-flow",
          "category-spending-report", "category-ranking", "financial-alerts",
          "advice", "crypto-holdings", "crypto-history",
        ]) {
          queryClient.invalidateQueries({ queryKey: [key] });
        }
      }
      setCurrency(data.currency);
    },
  });
}
