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

/**
 * Toujours relatif : les appels passent par le proxy same-origin
 * `src/app/api/[...path]/route.ts`, qui injecte `X-API-Key` côté serveur et
 * évite tout CORS. Avant, le navigateur tapait `NEXT_PUBLIC_API_URL` en absolu
 * (http://localhost:8000) : cross-origin — donc préflight à chaque lecture et
 * whitelist d'origines à tenir — et surtout aucun moyen d'authentifier depuis
 * la suppression du cookie posé par Jinja2.
 */
const API_BASE = '/api';

/**
 * `status: 0` = le backend n'a pas répondu du tout (process arrêté, mauvais
 * port, DNS, CORS). C'est le cas le plus fréquent en dev — `fetch` lève alors
 * un `TypeError: Failed to fetch` opaque, indistinguable d'un bug applicatif.
 * On le normalise ici pour que l'UI puisse afficher « backend injoignable »
 * plutôt qu'un message technique (cf. `isBackendUnreachable`).
 */
export class ApiError extends Error {
  constructor(public status: number, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'ApiError';
  }
}

export function isBackendUnreachable(error: unknown): boolean {
  return error instanceof ApiError && error.status === 0;
}

/**
 * Sans échéance, un backend éteint ne produit PAS d'erreur exploitable : selon
 * la configuration réseau, le SYN est refusé (échec immédiat) ou silencieusement
 * filtré — auquel cas `fetch` reste pendu jusqu'au timeout TCP de l'OS. Observé
 * en dev Windows : connexions bloquées en `SYN_SENT`, requête jamais résolue,
 * donc react-query reste `pending` et l'UI tourne indéfiniment.
 *
 * On borne donc chaque requête. `timeoutMs: 0` désactive l'échéance pour les
 * appels réellement longs ; les traitements lourds (sweep ML, backfill) sont
 * déjà asynchrones (`job_id` + polling) et n'en ont pas besoin.
 */
const DEFAULT_TIMEOUT_MS = 15_000;

type ApiFetchOptions = RequestInit & { timeoutMs?: number };

async function apiFetch<T>(endpoint: string, options: ApiFetchOptions = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...init } = options;

  // `Content-Type: application/json` sur une requête sans corps suffit à la
  // faire sortir des « simple requests » CORS : le navigateur émet alors un
  // préflight OPTIONS pour chaque lecture. Inutile ici, et coûteux vu le
  // sondage à 3 s. On ne pose l'en-tête que s'il y a effectivement un corps.
  const headers: Record<string, string> = {
    ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    ...((init.headers as Record<string, string>) || {}),
  };

  // Le signal fourni par l'appelant prime ; sinon on pose notre échéance.
  const signal = init.signal
    ?? (timeoutMs > 0 ? AbortSignal.timeout(timeoutMs) : undefined);

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${endpoint}`, {
      ...init, headers, signal, cache: 'no-store', credentials: 'include',
    });
  } catch (cause) {
    const timedOut = cause instanceof DOMException && cause.name === 'TimeoutError';
    throw new ApiError(
      0,
      timedOut
        ? `Backend injoignable sur ${API_BASE || 'origine courante'} — aucune réponse en ${timeoutMs / 1000} s. Le serveur FastAPI est-il démarré ?`
        : `Backend injoignable sur ${API_BASE || 'origine courante'} — le serveur FastAPI est-il démarré ?`,
      { cause },
    );
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.message || JSON.stringify(body);
    } catch {}
    // Le proxy same-origin répond 503 quand il n'atteint pas FastAPI : on le
    // ramène au même statut 0 qu'un échec réseau direct, pour que l'UI affiche
    // « Backend injoignable » plutôt qu'une erreur HTTP générique.
    if (res.status === 503 && /injoignable/i.test(String(detail))) {
      throw new ApiError(0, String(detail));
    }
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
  // S3-F1-US4 — Ventilation des frais (taker/maker/borrow/stop)
  getFeesBreakdown: (days = 30) => apiFetch<any>(`/stats/fees?days=${days}`),

  // ── Bots / Portfolio ────────────────────────────────────────────────────
  getBots: () => apiFetch<{ bots: Bot[]; counts: Record<string, number>; reopt_queue: string[]; thresholds: any }>('/bots'),
  getPortfolio: () => apiFetch<any>('/portfolio'),
  forceBotActive: (slotKey: string, enabled = true) =>
    apiFetch(`/bots/${encodeURIComponent(slotKey)}/force-active?enabled=${enabled}`, { method: 'POST' }),
  runBotForwardTest: (slotKey: string) =>
    apiFetch(`/bots/${encodeURIComponent(slotKey)}/forward-test`, { method: 'POST', timeoutMs: 0 }),
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
  // `timeoutMs: 0` : traitement synchrone potentiellement long (plusieurs
  // minutes selon la plage et le nombre de stratégies) — pas d'échéance.
  runBacktest: (payload: any) =>
    apiFetch<BacktestResult | BacktestResult[]>('/backtest', {
      method: 'POST',
      body: JSON.stringify(payload),
      timeoutMs: 0,
    }),
  // S5-F3-US1 — Cancel backtest
  cancelBacktest: () => apiFetch<{ status: string }>('/backtest/cancel', { method: 'POST' }),

  // ── Scanner ─────────────────────────────────────────────────────────────
  fastAnalysis: (symbol: string, tf = '1h') =>
    apiFetch<any>(`/scanner/fast-analysis?symbol=${encodeURIComponent(symbol)}&tf=${tf}`, { timeoutMs: 0 }),

  // S8-F4-US1 — Scanner multi-symboles
  scanMarket: (timeframe = '1h', limit = 50) =>
    apiFetch<any>(`/scanner?timeframe=${timeframe}&limit=${limit}`),
  // S8-F4-US2 — Top opportunités
  getOpportunities: (timeframe = '1h', limit = 10) =>
    apiFetch<any>(`/scanner/opportunities?timeframe=${timeframe}&limit=${limit}`),
  // S8-F4-US3 — Setups V11/V12 markers
  getSetupSeries: (symbol: string, timeframe = '1h', limit = 500, strategy = 'v11') =>
    apiFetch<any>(`/scanner/setup_series?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}&strategy=${strategy}`),
  // S8-F4-US4 — Signaux récents
  getSignals: (symbol: string, timeframe = '1h', limit = 300) =>
    apiFetch<any>(`/scanner/signals?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`),
  // S8-F4-US5 — Config scanner
  getScannerConfig: () => apiFetch<any>('/scanner/config'),

  // ── Univers ─────────────────────────────────────────────────────────────
  // S8-F2-US1 — Liste des univers
  getUniverses: () => apiFetch<any>('/universe'),
  // S8-F2-US2 — Membres d'un univers
  getUniverse: (name: string) => apiFetch<any>(`/universe/${encodeURIComponent(name)}`),
  // S8-F2-US3 — Ajouter/retirer symbole
  addUniverseSymbol: (universe: string, body: { symbol: string; name?: string; sector?: string; provider_symbol?: string }) =>
    apiFetch<any>(`/universe/${encodeURIComponent(universe)}/symbols`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  removeUniverseSymbol: (universe: string, symbol: string) =>
    apiFetch<any>(`/universe/${encodeURIComponent(universe)}/symbols/${encodeURIComponent(symbol)}`, {
      method: 'DELETE',
    }),

  // ── Data ────────────────────────────────────────────────────────────────
  getDataStatus: () => apiFetch<any>('/data/status'),
  refetchData: (symbol: string, tf: string) =>
    apiFetch<any>(`/data/refetch?symbol=${encodeURIComponent(symbol)}&tf=${tf}`, { method: 'POST', timeoutMs: 0 }),
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
  // Same-origin comme le reste : passe par le proxy, donc authentifié.
  optimizeStreamUrl: (jobId: string) => `${API_BASE}/optimize/stream?job_id=${jobId}`,

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
    return apiFetch<any>(`/replay?${q.toString()}`, { method: 'POST', timeoutMs: 0 });
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
