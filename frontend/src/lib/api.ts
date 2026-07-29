/**
 * Client API pour le backend FastAPI.
 * Côté client (navigateur) : utilise NEXT_PUBLIC_API_URL directement.
 * Côté serveur (SSR) : proxy relatif via Next.js rewrites.
 *
 * Auth (S1-05) : le cookie HttpOnly `api_key` (posé par les pages web, cf.
 * app/api/main.py::_tpl) est envoyé via `credentials: 'include'` — plus de
 * clé API dans une variable NEXT_PUBLIC_* (visible dans le bundle JS client).
 */

import type {
  BotStatus, Trade, Bot, SlotBudget, BacktestResult,
  ModelRegistryEntry, ModelArtifact, ModelDecision, MLJobStatus,
} from '@/types';

const API_BASE = typeof window !== 'undefined'
  ? (process.env.NEXT_PUBLIC_API_URL || '').replace(/\/$/, '') + '/api'
  : '/api';

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function apiFetch<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  };

  const res = await fetch(`${API_BASE}${endpoint}`, {
    ...options, headers, cache: 'no-store', credentials: 'include',
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.message || JSON.stringify(body);
    } catch {}
    throw new ApiError(res.status, `${endpoint}: ${detail}`);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  // ── Status / Health ─────────────────────────────────────────────────────
  getStatus: () => apiFetch<BotStatus>('/status'),
  getHealth: () => apiFetch<{ status: string; db: boolean; exchange: boolean; trader: boolean }>('/health'),

  // ── Bot control ─────────────────────────────────────────────────────────
  startBot: () => apiFetch<{ status: string }>('/bot/start', { method: 'POST' }),
  stopBot: (closePositions = false) =>
    apiFetch<{ status: string }>('/bot/stop', {
      method: 'POST',
      body: JSON.stringify({ close_positions: closePositions }),
    }),
  resetHalt: (force = false) =>
    apiFetch<{ status: string }>('/risk/reset-halt', {
      method: 'POST',
      body: JSON.stringify({ force }),
    }),

  // ── Trades ──────────────────────────────────────────────────────────────
  getTrades: (params: { limit?: number; offset?: number; symbol?: string; strategy?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.limit) q.set('limit', String(params.limit));
    if (params.offset) q.set('offset', String(params.offset));
    if (params.symbol) q.set('symbol', params.symbol);
    if (params.strategy) q.set('strategy', params.strategy);
    const qs = q.toString();
    return apiFetch<{ total: number; offset: number; limit: number; trades: Trade[] }>(
      `/trades${qs ? `?${qs}` : ''}`,
    );
  },
  exportTradesCsv: () => `${API_BASE}/trades/export?limit=50000`,
  getDailyStats: (days = 30) => apiFetch<any[]>(`/stats/daily?days=${days}`),

  // ── Bots / Portfolio ────────────────────────────────────────────────────
  getBots: () => apiFetch<{ bots: Bot[]; counts: Record<string, number>; reopt_queue: string[]; thresholds: any }>('/bots'),
  getPortfolio: () => apiFetch<any>('/portfolio'),
  forceBotActive: (slotKey: string, enabled = true) =>
    apiFetch(`/bots/${encodeURIComponent(slotKey)}/force-active?enabled=${enabled}`, { method: 'POST' }),
  runBotForwardTest: (slotKey: string) =>
    apiFetch(`/bots/${encodeURIComponent(slotKey)}/forward-test`, { method: 'POST' }),
  getOosTracker: () => apiFetch<any>('/oos-tracker'),

  // ── Slots ───────────────────────────────────────────────────────────────
  getSlots: () => apiFetch<{ capital: number; config: any; slots: SlotBudget[] }>('/slots'),
  setSlotBudget: (slotKey: string, budgetPct: number) =>
    apiFetch(`/slots/${encodeURIComponent(slotKey)}/budget?budget_pct=${budgetPct}`, { method: 'POST' }),
  toggleSlot: (slotKey: string, enabled: boolean) =>
    apiFetch(`/slots/${encodeURIComponent(slotKey)}/toggle?enabled=${enabled}`, { method: 'POST' }),
  resetSlot: (slotKey: string) =>
    apiFetch(`/slots/${encodeURIComponent(slotKey)}/reset`, { method: 'POST' }),
  forceRebalance: () => apiFetch('/slots/rebalance', { method: 'POST' }),
  resetSlotCircuitBreaker: (slotKey: string) =>
    apiFetch(`/circuit-breakers/reset/${encodeURIComponent(slotKey)}`, { method: 'POST' }),
  getCircuitBreakers: () => apiFetch<any>('/circuit-breakers'),

  // ── Config ──────────────────────────────────────────────────────────────
  getConfig: () => apiFetch<any>('/config'),
  // S5-01 : étendu pour accepter un `symbol` optionnel (override par symbole).
  // Si symbol est fourni, le backend écrit dans optimizer_results[tf][symbol]
  // au lieu de la section globale strategy_params.
  setStrategyParams: (strategy: string, params: Record<string, any>, symbol?: string) =>
    apiFetch('/config/strategy-params', {
      method: 'POST',
      body: JSON.stringify({ strategy, params, symbol }),
    }),
  // S5-01 : activation/désactivation de TF par symbole.
  toggleStrategyTimeframe: (tf: string, enable: boolean, symbol?: string) =>
    apiFetch('/config/strategy-timeframe', {
      method: 'POST',
      body: JSON.stringify({ tf, enable, symbol }),
    }),
  // Legacy aliases (compat with existing code)
  updateStrategyParams: (payload: any) =>
    apiFetch('/config/strategy-params', { method: 'POST', body: JSON.stringify(payload) }),
  toggleStrategyTimeframeLegacy: (payload: any) =>
    apiFetch('/config/strategy-timeframe', { method: 'POST', body: JSON.stringify(payload) }),
  setStrategies: (enabled: string[]) =>
    apiFetch(`/config/strategies?enabled=${enabled.join(',')}`, { method: 'POST' }),
  setTimeframes: (tfs: string[]) =>
    apiFetch(`/config/timeframes?timeframes=${tfs.join(',')}`, { method: 'POST' }),

  // ── Notifications / Settings ────────────────────────────────────────────
  getNotifications: (limit = 50, level = 'info') =>
    apiFetch<{ notifications: any[]; levels: string[] }>(`/notifications?limit=${limit}&level=${level}`),
  getPresets: () => apiFetch<any>('/settings/presets'),
  setRiskPreset: (preset: string) =>
    apiFetch(`/settings/risk-preset?preset=${preset}`, { method: 'POST' }),
  setExpertMode: (enabled: boolean) =>
    apiFetch(`/settings/expert-mode?enabled=${enabled}`, { method: 'POST' }),

  // ── Backtest ────────────────────────────────────────────────────────────
  getBacktestSettings: () => apiFetch<any>('/backtest/settings'),
  runBacktest: (payload: any) =>
    apiFetch<BacktestResult | BacktestResult[]>('/backtest', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // ── Scanner ─────────────────────────────────────────────────────────────
  fastAnalysis: (symbol: string, tf = '1h') =>
    apiFetch<any>(`/scanner/fast-analysis?symbol=${encodeURIComponent(symbol)}&tf=${tf}`),

  // ── Data ────────────────────────────────────────────────────────────────
  getDataStatus: () => apiFetch<any>('/data/status'),
  refetchData: (symbol: string, tf: string) =>
    apiFetch<any>(`/data/refetch?symbol=${encodeURIComponent(symbol)}&tf=${tf}`, { method: 'POST' }),
  // S5 (audit V2) : backfill des actions depuis l'UI (équivalent du bouton
  // qui existait dans la page Jinja2 /data). Lance en async côté backend.
  startBackfillEquities: (tf: string = '1d', years: number = 20) =>
    apiFetch<{ job_id: string; status: string; tf: string; years: number; univers: string[] }>(
      `/data/backfill-equities?tf=${tf}&years=${years}`,
      { method: 'POST' },
    ),
  getBackfillStatus: (jobId: string) =>
    apiFetch<{
      job_id: string;
      status: 'started' | 'done' | 'error';
      started_at: string;
      finished_at?: string;
      tf: string;
      years: number;
      univers: string[];
      progress: { done: number; total: number; current_symbol: string | null };
      results: Array<{ symbol: string; tf: string; bars: number; ok: boolean; error?: string }>;
      error: string | null;
    }>(`/data/backfill-status/${jobId}`),

  // ── Optimizer ───────────────────────────────────────────────────────────
  getOptimizeStatus: (jobId?: string) =>
    apiFetch<any>(`/optimize/status${jobId ? `?job_id=${jobId}` : ''}`),
  startOptimize: (params: {
    symbol?: string; symbols?: string; strategies?: string; timeframes?: string;
    method?: string; n_trials?: number; limit?: number; auto_apply?: boolean;
    n_jobs?: number; early_stop_patience?: number; ml_tune_hp?: boolean;
    param_search_optim?: boolean;
  }) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) q.set(k, String(v));
    });
    return apiFetch<any>(`/optimize/start?${q.toString()}`, { method: 'POST' });
  },
  applyOptimize: (jobId: string, force = false) =>
    apiFetch<any>(`/optimize/apply?job_id=${jobId}&force=${force}`, { method: 'POST' }),
  cancelOptimize: (jobId: string) =>
    apiFetch<any>(`/optimize/cancel?job_id=${jobId}`, { method: 'POST' }),
  deleteOptimizeJob: (jobId: string) =>
    apiFetch<any>(`/optimize/job?job_id=${jobId}`, { method: 'DELETE' }),
  getOptimizeResults: () => apiFetch<any>('/optimize/results'),
  getOptimizeSpaces: () => apiFetch<any>('/optimize/spaces'),
  optimizeStreamUrl: (jobId: string) => {
    const base = (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000').replace(/\/$/, '');
    return `${base}/api/optimize/stream?job_id=${jobId}`;
  },

  // ── ML ──────────────────────────────────────────────────────────────────
  getMLStrategyInfo: () => apiFetch<{ strategies: Record<string, any> }>('/ml/strategy-info'),
  getCandlesStats: () => apiFetch<{ store: any }>('/candles/stats'),

  // ── ML Model Registry (ML-02) ────────────────────────────────────────────
  getMLRegistry: () => apiFetch<{ models: ModelRegistryEntry[] }>('/ml/registry'),
  getMLRegistryVersions: (tf: string, recipe: string) =>
    apiFetch<{ versions: ModelArtifact[] }>(
      `/ml/registry/versions?${new URLSearchParams({ tf, recipe })}`,
    ),
  getMLRegistryDecisions: (tf: string, recipe: string, limit = 20) =>
    apiFetch<{ decisions: ModelDecision[] }>(
      `/ml/registry/decisions?${new URLSearchParams({ tf, recipe, limit: String(limit) })}`,
    ),
  pinModel: (tf: string, recipe: string, versionId: string) =>
    apiFetch<{ status: string }>('/ml/registry/pin', {
      method: 'POST', body: JSON.stringify({ tf, recipe, version_id: versionId }),
    }),
  unpinModel: (tf: string, recipe: string) =>
    apiFetch<{ status: string }>('/ml/registry/unpin', {
      method: 'POST', body: JSON.stringify({ tf, recipe }),
    }),
  promoteModel: (tf: string, recipe: string, versionId: string, decision: 'manual' | 'keep') =>
    apiFetch<{ status: string }>('/ml/registry/promote', {
      method: 'POST',
      body: JSON.stringify({
        tf, recipe, version_id: versionId, decision,
        reason: 'Action manuelle depuis la page Modèles (frontend)',
      }),
    }),
  startMLTrain: (params: {
    strategy: string; symbol: string; tf: string; as_of?: string | null;
    window_bars?: number | null; params?: Record<string, any>; publish?: boolean;
  }) => apiFetch<{ job_id: string }>('/ml/train', { method: 'POST', body: JSON.stringify(params) }),
  getMLTrainStatus: (jobId: string) =>
    apiFetch<MLJobStatus>(`/ml/train/status?${new URLSearchParams({ job_id: jobId })}`),
  startMLSweep: (params: {
    strategy: string; symbol: string; tf: string; windows: number[];
    as_of?: string | null; params?: Record<string, any>; publish_best?: boolean;
  }) => apiFetch<{ job_id: string }>('/ml/sweep', { method: 'POST', body: JSON.stringify(params) }),
  getMLSweepStatus: (jobId: string) =>
    apiFetch<MLJobStatus>(`/ml/sweep/status?${new URLSearchParams({ job_id: jobId })}`),

  // ── Derivatives ─────────────────────────────────────────────────────────
  getDerivativesData: (symbol = 'BTC/USDC', period = '1h', limit = 1000, refresh = false) =>
    apiFetch<any>(`/derivatives/data?symbol=${encodeURIComponent(symbol)}&period=${period}&limit=${limit}&refresh=${refresh}`),
  getDerivativesStatus: (symbol = 'BTC/USDC') =>
    apiFetch<any>(`/derivatives/status?symbol=${encodeURIComponent(symbol)}`),

  // ── Replay ──────────────────────────────────────────────────────────────
  runReplay: (params: {
    symbol?: string; months?: number; timeframes?: string; strategies?: string;
    walk_forward?: boolean; monte_carlo?: boolean;
  }) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) q.set(k, String(v));
    });
    return apiFetch<any>(`/replay?${q.toString()}`, { method: 'POST' });
  },
  cancelReplay: () => apiFetch<any>('/replay/cancel', { method: 'POST' }),

  // ── Audit / Audit Log ───────────────────────────────────────────────────
  getAuditResults: () => apiFetch<any>('/audit/results'),
  getStrategyPerformance: (slotKey: string) =>
    apiFetch<any>(`/strategy/${encodeURIComponent(slotKey)}/performance`),
  getAuditLog: (params: { limit?: number; offset?: number; action?: string; actor?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.limit) q.set('limit', String(params.limit));
    if (params.offset) q.set('offset', String(params.offset));
    if (params.action) q.set('action', params.action);
    if (params.actor) q.set('actor', params.actor);
    const qs = q.toString();
    return apiFetch<{ events: any[]; total: number; limit: number; offset: number }>(
      `/audit/log${qs ? `?${qs}` : ''}`,
    );
  },
  getAuditLogStats: () => apiFetch<{ by_action: Record<string, number>; total: number; last_event: any }>('/audit/log/stats'),

  // ── SMC / Scanner ───────────────────────────────────────────────────────
  getSMC: (symbol = 'BTC/USDC', timeframe = '1h', limit = 1000) =>
    apiFetch<any>(`/scanner/smc?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`),
  getSMCReplay: (symbol = 'BTC/USDC', timeframe = '4h', limit = 800) =>
    apiFetch<any>(`/scanner/smc_replay?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`),
  getSignals: (symbol = 'BTC/USDC', timeframe = '1h', limit = 300) =>
    apiFetch<any>(`/scanner/signals?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`),
};

export { ApiError };
