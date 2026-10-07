import { api } from "@/api/client";
import type { Budget, BudgetInput, BudgetStatusResponse } from "@/types";

export function fetchBudgets() {
  return api.get<Budget[]>("/budgets");
}

export function fetchBudgetStatus(year: number, month: number) {
  return api.get<BudgetStatusResponse>(`/budgets/status?year=${year}&month=${month}`);
}

export function createBudget(input: BudgetInput) {
  return api.post<Budget>("/budgets", input);
}

export function updateBudget(id: number, monthlyLimit: string, currency: string) {
  return api.patch<Budget>(`/budgets/${id}`, { monthly_limit: monthlyLimit, currency });
}

export function deleteBudget(id: number) {
  return api.delete<void>(`/budgets/${id}`);
}
