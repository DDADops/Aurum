import { api } from "@/api/client";
import type { RecurringTransaction, RecurringTransactionInput, RecurringTransactionUpdateInput } from "@/types";

export function fetchRecurring() {
  return api.get<RecurringTransaction[]>("/recurring");
}

export function createRecurring(input: RecurringTransactionInput) {
  return api.post<RecurringTransaction>("/recurring", input);
}

export function updateRecurring(id: number, input: RecurringTransactionUpdateInput) {
  return api.patch<RecurringTransaction>(`/recurring/${id}`, input);
}

export function deleteRecurring(id: number) {
  return api.delete<void>(`/recurring/${id}`);
}

export function postRecurring(id: number) {
  return api.post<RecurringTransaction>(`/recurring/${id}/post`, {});
}
