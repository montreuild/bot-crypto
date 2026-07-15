/**
 * Hooks TanStack Query pour les données API.
 * Utilise un polling court pour les données temps réel (status, positions).
 */

'use client';

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { BotStatus } from '@/types';

// ── Bot status (polling 3s) ─────────────────────────────────────────────────
export function useBotStatus() {
  return useQuery<BotStatus>({
    queryKey: ['status'],
    queryFn: api.getStatus,
    refetchInterval: 3000, // 3s — temps réel via polling, complété par WS
    refetchOnWindowFocus: true,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: api.getHealth,
    refetchInterval: 10000,
  });
}

// ── Trades ──────────────────────────────────────────────────────────────────
export function useTrades(params: { limit?: number; offset?: number; symbol?: string; strategy?: string } = {}) {
  return useQuery({
    queryKey: ['trades', params],
    queryFn: () => api.getTrades(params),
    refetchInterval: 15000,
  });
}

export function useDailyStats(days = 30) {
  return useQuery({
    queryKey: ['dailyStats', days],
    queryFn: () => api.getDailyStats(days),
    refetchInterval: 60000,
  });
}

// ── Bots ────────────────────────────────────────────────────────────────────
export function useBots() {
  return useQuery({
    queryKey: ['bots'],
    queryFn: api.getBots,
    refetchInterval: 10000,
  });
}

export function usePortfolio() {
  return useQuery({
    queryKey: ['portfolio'],
    queryFn: api.getPortfolio,
    refetchInterval: 5000,
  });
}

export function useForceBotActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slotKey, enabled }: { slotKey: string; enabled: boolean }) =>
      api.forceBotActive(slotKey, enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bots'] });
      qc.invalidateQueries({ queryKey: ['portfolio'] });
    },
  });
}

export function useRunForwardTest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (slotKey: string) => api.runBotForwardTest(slotKey),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bots'] });
    },
  });
}

// ── Slots ───────────────────────────────────────────────────────────────────
export function useSlots() {
  return useQuery({
    queryKey: ['slots'],
    queryFn: api.getSlots,
    refetchInterval: 10000,
  });
}

export function useSetSlotBudget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slotKey, budgetPct }: { slotKey: string; budgetPct: number }) =>
      api.setSlotBudget(slotKey, budgetPct),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['slots'] }),
  });
}

export function useToggleSlot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slotKey, enabled }: { slotKey: string; enabled: boolean }) =>
      api.toggleSlot(slotKey, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['slots'] }),
  });
}

// ── Bot control ─────────────────────────────────────────────────────────────
export function useStartBot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.startBot,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  });
}

export function useStopBot() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (closePositions: boolean) => api.stopBot(closePositions),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  });
}

export function useResetHalt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (force: boolean) => api.resetHalt(force),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['status'] }),
  });
}

// ── Circuit breakers ────────────────────────────────────────────────────────
export function useCircuitBreakers() {
  return useQuery({
    queryKey: ['circuitBreakers'],
    queryFn: api.getCircuitBreakers,
    refetchInterval: 5000,
  });
}

// ── Notifications ───────────────────────────────────────────────────────────
export function useNotifications(limit = 50, level = 'info') {
  return useQuery({
    queryKey: ['notifications', limit, level],
    queryFn: () => api.getNotifications(limit, level),
    refetchInterval: 10000,
  });
}

// ── Settings ────────────────────────────────────────────────────────────────
export function usePresets() {
  return useQuery({
    queryKey: ['presets'],
    queryFn: api.getPresets,
  });
}

export function useSetRiskPreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (preset: string) => api.setRiskPreset(preset),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['presets'] });
      qc.invalidateQueries({ queryKey: ['status'] });
    },
  });
}

// ── Config ──────────────────────────────────────────────────────────────────
export function useConfig() {
  return useQuery({
    queryKey: ['config'],
    queryFn: api.getConfig,
  });
}

// ── Audit ───────────────────────────────────────────────────────────────────
export function useAuditResults() {
  return useQuery({
    queryKey: ['audit'],
    queryFn: api.getAuditResults,
    refetchInterval: 30000,
  });
}

// ── Backtest ────────────────────────────────────────────────────────────────
export function useBacktestSettings() {
  return useQuery({
    queryKey: ['backtestSettings'],
    queryFn: api.getBacktestSettings,
    staleTime: 5 * 60 * 1000, // 5 min
  });
}

export function useRunBacktest() {
  return useMutation({
    mutationFn: (payload: any) => api.runBacktest(payload),
  });
}

// ── Strategy performance ────────────────────────────────────────────────────
export function useStrategyPerformance(slotKey: string | null) {
  return useQuery({
    queryKey: ['strategyPerf', slotKey],
    queryFn: () => api.getStrategyPerformance(slotKey!),
    enabled: !!slotKey,
  });
}
