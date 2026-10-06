import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createAccount, deleteAccount, fetchAccounts, updateAccount } from "@/api/accounts";
import type { AccountInput } from "@/types";

export function useAccounts(includeArchived = false) {
  return useQuery({ queryKey: ["accounts", includeArchived], queryFn: () => fetchAccounts(includeArchived) });
}

export function useCreateAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: AccountInput) => createAccount(input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["accounts"] }),
  });
}

export function useUpdateAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<AccountInput> & { is_archived?: boolean } }) =>
      updateAccount(id, input),
    // A concurrent first posting can reject a formerly eligible currency change.
    onError: () => queryClient.invalidateQueries({ queryKey: ["accounts"] }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["accounts"] }),
  });
}

export function useDeleteAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => deleteAccount(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      // A cascade-deleted account takes its transaction history with it —
      // refresh everything derived from transactions, same set posting a
      // recurring transaction invalidates.
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      queryClient.invalidateQueries({ queryKey: ["net-worth-summary"] });
      queryClient.invalidateQueries({ queryKey: ["category-spending-report"] });
      queryClient.invalidateQueries({ queryKey: ["category-ranking"] });
      queryClient.invalidateQueries({ queryKey: ["budget-status"] });
      queryClient.invalidateQueries({ queryKey: ["financial-alerts"] });
      queryClient.invalidateQueries({ queryKey: ["advice"] });
      queryClient.invalidateQueries({ queryKey: ["cash-flow"] });
    },
  });
}
