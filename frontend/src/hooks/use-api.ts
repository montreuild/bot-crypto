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

export function useSetExpertMode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => api.setExpertMode(enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['presets'] }),
  });
}

// ── Config ──────────────────────────────────────────────────────────────────
export function useConfig() {
  return useQuery({
    queryKey: ['config'],
    queryFn: api.getConfig,
  });
}

// S5-01 : Permettre de sauver des params de stratégie par symbole (override).
// Le backend étend /api/config/strategy-params pour accepter un `symbol` optionnel.
export function useSetStrategyParams() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { strategy: string; params: Record<string, any>; symbol?: string }) =>
      api.setStrategyParams(args.strategy, args.params, args.symbol),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['config'] });
      qc.invalidateQueries({ queryKey: ['audit'] });
    },
  });
}

// S5-01 : Activation/désactivation d'une TF par symbole.
export function useToggleStrategyTimeframe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { tf: string; enable: boolean; symbol?: string }) =>
      api.toggleStrategyTimeframe(args.tf, args.enable, args.symbol),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['config'] });
      qc.invalidateQueries({ queryKey: ['status'] });
    },
  });
}

// S5-01 : alias pour useBotStatus (pour la page Config qui a besoin des symboles du scanner).
export function useApiStatus() {
  return useBotStatus();
}

// ── Audit ───────────────────────────────────────────────────────────────────
export function useAuditResults() {
  return useQuery({
    queryKey: ['audit'],
    queryFn: api.getAuditResults,
    refetchInterval: 30000,
  });
}

// ── Audit Log ───────────────────────────────────────────────────────────────
export function useAuditLog(params: { limit?: number; offset?: number; action?: string; actor?: string } = {}) {
  return useQuery({
    queryKey: ['auditLog', params],
    queryFn: () => api.getAuditLog(params),
    refetchInterval: 10000,
  });
}

export function useAuditLogStats() {
  return useQuery({
    queryKey: ['auditLogStats'],
    queryFn: api.getAuditLogStats,
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

// ── Optimizer ───────────────────────────────────────────────────────────────
export function useOptimizeStatus(jobId?: string) {
  return useQuery({
    queryKey: ['optimizeStatus', jobId],
    queryFn: () => api.getOptimizeStatus(jobId),
    refetchInterval: 1500, // polling rapide pour suivi temps réel
  });
}

export function useOptimizeSpaces() {
  return useQuery({
    queryKey: ['optimizeSpaces'],
    queryFn: api.getOptimizeSpaces,
    staleTime: 5 * 60 * 1000,
  });
}

export function useOptimizeResults() {
  return useQuery({
    queryKey: ['optimizeResults'],
    queryFn: api.getOptimizeResults,
    refetchInterval: 30000,
  });
}

export function useStartOptimize() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: any) => api.startOptimize(params),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['optimizeStatus'] });
    },
  });
}

export function useApplyOptimize() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, force }: { jobId: string; force?: boolean }) =>
      api.applyOptimize(jobId, force),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['optimizeStatus'] });
      qc.invalidateQueries({ queryKey: ['optimizeResults'] });
      qc.invalidateQueries({ queryKey: ['audit'] });
    },
  });
}

export function useCancelOptimize() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => api.cancelOptimize(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['optimizeStatus'] }),
  });
}

export function useDeleteOptimizeJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => api.deleteOptimizeJob(jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['optimizeStatus'] }),
  });
}

// ── ML ──────────────────────────────────────────────────────────────────────
export function useMLStrategyInfo() {
  return useQuery({
    queryKey: ['mlInfo'],
    queryFn: api.getMLStrategyInfo,
    refetchInterval: 30000,
  });
}

export function useCandlesStats() {
  return useQuery({
    queryKey: ['candlesStats'],
    queryFn: api.getCandlesStats,
    refetchInterval: 30000,
  });
}

// ── ML Model Registry (ML-02) ────────────────────────────────────────────────
export function useMLRegistry() {
  return useQuery({
    queryKey: ['mlRegistry'],
    queryFn: api.getMLRegistry,
    refetchInterval: 30000,
  });
}

export function useMLRegistryVersions(tf: string | null, recipe: string | null) {
  return useQuery({
    queryKey: ['mlRegistryVersions', tf, recipe],
    queryFn: () => api.getMLRegistryVersions(tf!, recipe!),
    enabled: !!tf && !!recipe,
  });
}

export function useMLRegistryDecisions(tf: string | null, recipe: string | null) {
  return useQuery({
    queryKey: ['mlRegistryDecisions', tf, recipe],
    queryFn: () => api.getMLRegistryDecisions(tf!, recipe!),
    enabled: !!tf && !!recipe,
  });
}

// Invalide la vue d'ensemble ET les détails (versions/décisions) déjà montés
// pour toute recette — nécessaire pour que le tableau déplié (pin/décision)
// reflète immédiatement une action, comme le fait la page Jinja équivalente.
function invalidateModelRegistryQueries(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ['mlRegistry'] });
  qc.invalidateQueries({ queryKey: ['mlRegistryVersions'] });
  qc.invalidateQueries({ queryKey: ['mlRegistryDecisions'] });
}

export function usePinModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tf, recipe, versionId }: { tf: string; recipe: string; versionId: string }) =>
      api.pinModel(tf, recipe, versionId),
    onSuccess: () => invalidateModelRegistryQueries(qc),
  });
}

export function useUnpinModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tf, recipe }: { tf: string; recipe: string }) =>
      api.unpinModel(tf, recipe),
    onSuccess: () => invalidateModelRegistryQueries(qc),
  });
}

export function usePromoteModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ tf, recipe, versionId, decision }:
      { tf: string; recipe: string; versionId: string; decision: 'manual' | 'keep' }) =>
      api.promoteModel(tf, recipe, versionId, decision),
    onSuccess: () => invalidateModelRegistryQueries(qc),
  });
}

export function useStartMLTrain() {
  return useMutation({ mutationFn: api.startMLTrain });
}

export function useMLTrainStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['mlTrainStatus', jobId],
    queryFn: () => api.getMLTrainStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1500 : false),
  });
}

export function useStartMLSweep() {
  return useMutation({ mutationFn: api.startMLSweep });
}

export function useMLSweepStatus(jobId: string | null) {
  return useQuery({
    queryKey: ['mlSweepStatus', jobId],
    queryFn: () => api.getMLSweepStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1500 : false),
  });
}

// ── Derivatives ─────────────────────────────────────────────────────────────
export function useDerivativesData(symbol = 'BTC/USDC', period = '1h', refresh = false) {
  return useQuery({
    queryKey: ['derivatives', symbol, period, refresh],
    queryFn: () => api.getDerivativesData(symbol, period, 1000, refresh),
    refetchInterval: 60000,
  });
}

export function useDerivativesStatus(symbol = 'BTC/USDC') {
  return useQuery({
    queryKey: ['derivativesStatus', symbol],
    queryFn: () => api.getDerivativesStatus(symbol),
  });
}

// ── Replay ──────────────────────────────────────────────────────────────────
export function useRunReplay() {
  return useMutation({
    mutationFn: (params: any) => api.runReplay(params),
  });
}

export function useCancelReplay() {
  return useMutation({
    mutationFn: api.cancelReplay,
  });
}

// ── Data ────────────────────────────────────────────────────────────────────
export function useDataStatus() {
  return useQuery({
    queryKey: ['dataStatus'],
    queryFn: api.getDataStatus,
    refetchInterval: 30000,
  });
}

export function useRefetchData() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ symbol, tf }: { symbol: string; tf: string }) => api.refetchData(symbol, tf),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dataStatus'] }),
  });
}

// ── SMC / Scanner ───────────────────────────────────────────────────────────
export function useSMC(symbol: string, timeframe: string, limit: number, enabled = true) {
  return useQuery({
    queryKey: ['smc', symbol, timeframe, limit],
    queryFn: () => api.getSMC(symbol, timeframe, limit),
    enabled,
    staleTime: 30 * 1000,
  });
}

export function useSMCReplay(symbol: string, timeframe: string, limit: number, enabled = true) {
  return useQuery({
    queryKey: ['smcReplay', symbol, timeframe, limit],
    queryFn: () => api.getSMCReplay(symbol, timeframe, limit),
    enabled,
    staleTime: 60 * 1000,
  });
}

export function useSignals(symbol: string, timeframe: string, limit: number, enabled = true) {
  return useQuery({
    queryKey: ['signals', symbol, timeframe, limit],
    queryFn: () => api.getSignals(symbol, timeframe, limit),
    enabled,
    staleTime: 30 * 1000,
  });
}
