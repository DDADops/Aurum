import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addGoalContribution, createGoal, deleteGoal, fetchGoals, updateGoal } from "@/api/goals";
import type { GoalContributionInput, GoalInput } from "@/types";

function useInvalidateGoals() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["goals"] });
}

export function useGoals() {
  return useQuery({ queryKey: ["goals"], queryFn: fetchGoals });
}

export function useCreateGoal() {
  const invalidate = useInvalidateGoals();
  return useMutation({
    mutationFn: (input: GoalInput) => createGoal(input),
    onSuccess: invalidate,
    onError: invalidate,
  });
}

export function useUpdateGoal() {
  const invalidate = useInvalidateGoals();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<GoalInput> }) => updateGoal(id, input),
    onSuccess: invalidate,
    onError: invalidate,
  });
}

export function useDeleteGoal() {
  const invalidate = useInvalidateGoals();
  return useMutation({
    mutationFn: (id: number) => deleteGoal(id),
    onSuccess: invalidate,
    onError: invalidate,
  });
}

export function useAddGoalContribution() {
  const invalidate = useInvalidateGoals();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: GoalContributionInput }) => addGoalContribution(id, input),
    onSuccess: invalidate,
    onError: invalidate,
  });
}
