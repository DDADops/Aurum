import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createRecurring,
  deleteRecurring,
  fetchRecurring,
  postRecurring,
  updateRecurring,
} from "@/api/recurring";
import type { RecurringTransactionInput, RecurringTransactionUpdateInput } from "@/types";

function useInvalidateRecurring() {
  const queryClient = useQueryClient();
  return (alsoInvalidateTransactions: boolean) => {
    queryClient.invalidateQueries({ queryKey: ["recurring"] });
    queryClient.invalidateQueries({ queryKey: ["accounts"] });
    if (alsoInvalidateTransactions) {
      // Posting creates a real Transaction — refresh everything derived from it.
      queryClient.invalidateQueries({ queryKey: ["transactions"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard-summary"] });
      queryClient.invalidateQueries({ queryKey: ["net-worth-summary"] });
      queryClient.invalidateQueries({ queryKey: ["category-spending-report"] });
      queryClient.invalidateQueries({ queryKey: ["category-ranking"] });
      queryClient.invalidateQueries({ queryKey: ["budget-status"] });
      queryClient.invalidateQueries({ queryKey: ["financial-alerts"] });
      queryClient.invalidateQueries({ queryKey: ["advice"] });
      queryClient.invalidateQueries({ queryKey: ["cash-flow"] });
    }
  };
}

export function useRecurring() {
  return useQuery({ queryKey: ["recurring"], queryFn: fetchRecurring });
}

export function useCreateRecurring() {
  const invalidate = useInvalidateRecurring();
  return useMutation({
    mutationFn: (input: RecurringTransactionInput) => createRecurring(input),
    onSuccess: () => invalidate(false),
    onError: () => invalidate(false),
    retry: false,
  });
}

export function useUpdateRecurring() {
  const invalidate = useInvalidateRecurring();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: RecurringTransactionUpdateInput }) =>
      updateRecurring(id, input),
    onSuccess: () => invalidate(false),
    onError: () => invalidate(false),
    retry: false,
  });
}

export function useDeleteRecurring() {
  const invalidate = useInvalidateRecurring();
  return useMutation({
    mutationFn: (id: number) => deleteRecurring(id),
    onSuccess: () => invalidate(false),
    onError: () => invalidate(false),
    retry: false,
  });
}

export function usePostRecurring() {
  const invalidate = useInvalidateRecurring();
  return useMutation({
    mutationFn: (id: number) => postRecurring(id),
    onSuccess: () => invalidate(true),
    onError: () => invalidate(true),
    retry: false,
  });
}
