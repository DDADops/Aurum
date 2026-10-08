import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { addAssetValuation, createAsset, deleteAsset, fetchAssets, updateAsset } from "@/api/assets";
import type { AssetInput, AssetUpdateInput, AssetValuationInput } from "@/types";

function useInvalidateNetWorth() {
  const queryClient = useQueryClient();
  return () => Promise.all([
      queryClient.invalidateQueries({ queryKey: ["assets"], refetchType: "all" }),
      queryClient.invalidateQueries({ queryKey: ["net-worth-summary"] }),
  ]);
}

export function useAssets() {
  return useQuery({ queryKey: ["assets"], queryFn: fetchAssets });
}

export function useCreateAsset() {
  const invalidate = useInvalidateNetWorth();
  return useMutation({
    mutationFn: (input: AssetInput) => createAsset(input),
    onSuccess: invalidate,
    onError: invalidate,
    retry: false,
  });
}

export function useUpdateAsset() {
  const invalidate = useInvalidateNetWorth();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: AssetUpdateInput }) => updateAsset(id, input),
    onSuccess: invalidate,
    onError: invalidate,
    retry: false,
  });
}

export function useAddAssetValuation() {
  const invalidate = useInvalidateNetWorth();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: AssetValuationInput }) => addAssetValuation(id, input),
    onSuccess: invalidate,
    onError: invalidate,
    retry: false,
  });
}

export function useDeleteAsset() {
  const invalidate = useInvalidateNetWorth();
  return useMutation({
    mutationFn: (id: number) => deleteAsset(id),
    onSuccess: invalidate,
  });
}
