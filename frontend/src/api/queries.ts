import { useQuery } from '@tanstack/react-query'

import { apiClient } from './client'
import type { Asset, Fill, Order, Page, Position, StatusResponse, StrategyRun } from './domain'

const recentPath = (resource: string) => `/${resource}?limit=100` as `/${string}`

export function useStatus() {
  return useQuery({ queryKey: ['status'], queryFn: () => apiClient.get<StatusResponse>('/status') })
}

export function useAssets() {
  return useQuery({ queryKey: ['assets'], queryFn: () => apiClient.get<Page<Asset>>(recentPath('assets')) })
}

export function useAsset(assetId: number | null) {
  return useQuery({
    queryKey: ['asset', assetId],
    queryFn: () => apiClient.get<Asset>(`/assets/${assetId}`),
    enabled: assetId !== null,
    retry: (count, error) => !(error instanceof Error && 'status' in error && error.status === 404) && count < 2,
  })
}

export function usePositions() {
  return useQuery({ queryKey: ['positions'], queryFn: () => apiClient.get<Page<Position>>(recentPath('positions')) })
}

export function useRuns() {
  return useQuery({ queryKey: ['runs'], queryFn: () => apiClient.get<Page<StrategyRun>>(recentPath('runs')) })
}

export function useOrders() {
  return useQuery({ queryKey: ['orders'], queryFn: () => apiClient.get<Page<Order>>(recentPath('orders')) })
}

export function useFills() {
  return useQuery({ queryKey: ['fills'], queryFn: () => apiClient.get<Page<Fill>>(recentPath('fills')) })
}
