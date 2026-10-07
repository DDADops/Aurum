import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createBudget, deleteBudget, fetchBudgetStatus, fetchBudgets, updateBudget } from "@/api/budgets";
import type { BudgetInput } from "@/types";

function useInvalidateBudgets() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["budgets"] });
    queryClient.invalidateQueries({ queryKey: ["budget-status"] });
    queryClient.invalidateQueries({ queryKey: ["financial-alerts"] });
  };
}

export function useBudgets() {
  return useQuery({ queryKey: ["budgets"], queryFn: fetchBudgets });
}

export function useBudgetStatus(year: number, month: number) {
  return useQuery({ queryKey: ["budget-status", year, month], queryFn: () => fetchBudgetStatus(year, month) });
}

export function useCreateBudget() {
  const invalidate = useInvalidateBudgets();
  return useMutation({
    mutationFn: (input: BudgetInput) => createBudget(input),
    onSuccess: invalidate,
    onError: invalidate,
  });
}

export function useUpdateBudget() {
  const invalidate = useInvalidateBudgets();
  return useMutation({
    mutationFn: ({ id, monthlyLimit, currency }: { id: number; monthlyLimit: string; currency: string }) => updateBudget(id, monthlyLimit, currency),
    onSuccess: invalidate,
    onError: invalidate,
  });
}

export function useDeleteBudget() {
  const invalidate = useInvalidateBudgets();
  return useMutation({
    mutationFn: (id: number) => deleteBudget(id),
    onSuccess: invalidate,
    onError: invalidate,
  });
}
