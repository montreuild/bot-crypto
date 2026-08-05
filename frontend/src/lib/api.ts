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
  RiskOverview, RiskDiagnostics, VenueEnvelopeConfig,
} from '@/types';
import {
  BotStatusSchema, BotsResponseSchema, OosTrackerSchema, MlRecipesResponseSchema,
  DailyStatsSchema, FeesBreakdownSchema, HealthSchema, UniversesSchema,
  OptimizeStatusSchema, OptimizeResultsSchema, OptimizeSpacesSchema,
  MlRegistrySchema, DerivativesDataSchema, SmcSchema, ScannerSignalsSchema,
  ScannerConfigSchema, BacktestSchema, RiskSchema, RiskDiagnosticsSchema,
} from '@/lib/schemas';

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

/**
 * `base` permet de sortir du préfixe `/api` pour les rares routes montées à la
 * racine du backend — aujourd'hui `/health` et `/metrics`, volontairement sans
 * auth (cf. `app/api/main.py`). `next.config.mjs` proxifie déjà `/health` vers
 * le backend via `rewrites()`, il n'y a donc rien de plus à câbler.
 */
/**
 * `schema` valide la forme de la réponse. Volontairement **non bloquant** :
 * en cas d'écart on journalise et on renvoie la donnée brute, plutôt que de
 * faire échouer la requête. L'objectif est de transformer un crash de page en
 * avertissement traçable — c'est exactement le scénario qui a fait tomber
 * `/ml` et le drawer de `/bots` (cf. `schemas.ts`).
 */
type ApiFetchOptions = RequestInit & {
  timeoutMs?: number;
  base?: string;
  schema?: { safeParse: (data: unknown) => { success: boolean; data?: unknown; error?: unknown } };
};

async function apiFetch<T>(endpoint: string, options: ApiFetchOptions = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, base = API_BASE, schema, ...init } = options;

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
    res = await fetch(`${base}${endpoint}`, {
      ...init, headers, signal, cache: 'no-store', credentials: 'include',
    });
  } catch (cause) {
    const timedOut = cause instanceof DOMException && cause.name === 'TimeoutError';
    throw new ApiError(
      0,
      timedOut
        ? `Backend injoignable sur ${base || 'origine courante'} — aucune réponse en ${timeoutMs / 1000} s. Le serveur FastAPI est-il démarré ?`
        : `Backend injoignable sur ${base || 'origine courante'} — le serveur FastAPI est-il démarré ?`,
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

  const payload = await res.json();
  if (!schema) return payload as T;

  const parsed = schema.safeParse(payload);
  if (parsed.success) return parsed.data as T;

  // Contrat rompu : on ne casse pas l'UI, mais l'écart doit être visible.
  // eslint-disable-next-line no-console
  console.warn(
    `[api] ${endpoint} : la réponse ne correspond pas au schéma attendu. ` +
      'Les composants qui en dépendent peuvent se comporter anormalement.',
    parsed.error,
  );
  return payload as T;
}

export const api = {
  // ── Status / Health ─────────────────────────────────────────────────────
  getStatus: () => apiFetch<BotStatus>('/status', { schema: BotStatusSchema }),
  // `/health` est monté à la RACINE du backend, pas sous `/api` : l'appeler via
  // le préfixe donnait `/api/health` → 404 systématique, et l'indicateur de
  // santé de la topbar restait donc éternellement vide.
  getHealth: () =>
    apiFetch<{ status: string; db: boolean; exchange: boolean; trader: boolean }>(
      '/health', { base: '', schema: HealthSchema },
    ),

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
  getDailyStats: (days = 30) => apiFetch<any[]>(`/stats/daily?days=${days}`, { schema: DailyStatsSchema }),
  // S3-F1-US4 — Ventilation des frais (taker/maker/borrow/stop)
  getFeesBreakdown: (days = 30) => apiFetch<any>(`/stats/fees?days=${days}`, { schema: FeesBreakdownSchema }),

  // ── Bots / Portfolio ────────────────────────────────────────────────────
  getBots: () => apiFetch<{ bots: Bot[]; counts: Record<string, number>; reopt_queue: string[]; thresholds: any }>('/bots', { schema: BotsResponseSchema }),
  getPortfolio: () => apiFetch<any>('/portfolio'),
  forceBotActive: (slotKey: string, enabled = true) =>
    apiFetch(`/bots/${encodeURIComponent(slotKey)}/force-active?enabled=${enabled}`, { method: 'POST' }),
  runBotForwardTest: (slotKey: string) =>
    apiFetch(`/bots/${encodeURIComponent(slotKey)}/forward-test`, { method: 'POST', timeoutMs: 0 }),
  getOosTracker: () => apiFetch<any>('/oos-tracker', { schema: OosTrackerSchema }),

  // ── Enveloppes de risque (S12) ──────────────────────────────────────────
  getRisk: () => apiFetch<RiskOverview>('/risk', { schema: RiskSchema }),
  getRiskDiagnostics: () =>
    apiFetch<RiskDiagnostics>('/risk/diagnostics', { schema: RiskDiagnosticsSchema }),
  /** `envelopes` : { venue: { capital?, max_symbol_exposure_pct?, ... } }.
   *  Répond 400 si l'édition rendrait une config structurellement impossible. */
  updateEnvelopes: (envelopes: Record<string, VenueEnvelopeConfig>) =>
    apiFetch<{ envelopes: Record<string, VenueEnvelopeConfig>; saved_to_disk: boolean }>(
      '/risk/envelopes', { method: 'POST', body: JSON.stringify(envelopes) },
    ),

  // ── Slots ───────────────────────────────────────────────────────────────
  getSlots: () => apiFetch<{ capital: number; slots: SlotBudget[] }>('/slots'),
  toggleSlot: (slotKey: string, enabled: boolean) =>
    apiFetch(`/slots/${encodeURIComponent(slotKey)}/toggle?enabled=${enabled}`, { method: 'POST' }),
  resetSlot: (slotKey: string) =>
    apiFetch(`/slots/${encodeURIComponent(slotKey)}/reset`, { method: 'POST' }),
  resetSlotCircuitBreaker: (slotKey: string) =>
    apiFetch(`/circuit-breakers/reset/${encodeURIComponent(slotKey)}`, { method: 'POST' }),
  getCircuitBreakers: () => apiFetch<any>('/circuit-breakers'),

  // ── Config ──────────────────────────────────────────────────────────────
  getConfig: () => apiFetch<any>('/config'),
  /**
   * POST /api/config/trading — paramètres de trading globaux (SEC-03 body JSON).
   *
   * L'endpoint existait côté backend depuis toujours et l'audit le listait
   * « ✅ consommé (useUpdateTradingConfig) », mais aucune méthode ni aucun hook
   * ne l'appelait : `paper_mode` n'était QUE lu et affiché dans /config. Il
   * n'existait donc aucun moyen de basculer paper ↔ live depuis l'UI.
   */
  updateTradingConfig: (params: {
    score_threshold?: number;
    paper_mode?: boolean;
    paper_slippage?: number;
    daily_drawdown_limit?: number;
  }) =>
    apiFetch<{
      changed: Record<string, unknown>;
      saved_to_disk: boolean;
      trader_updated: boolean;
    }>('/config/trading', {
      method: 'POST',
      body: JSON.stringify(params),
    }),
  // S5-01 / SEC-03 : body StrategyParamsBody (strategy, params, timeframe?, symbol?).
  setStrategyParams: (
    strategy: string,
    params: Record<string, any>,
    opts?: { symbol?: string; timeframe?: string },
  ) =>
    apiFetch('/config/strategy-params', {
      method: 'POST',
      body: JSON.stringify({
        strategy,
        params,
        symbol: opts?.symbol,
        timeframe: opts?.timeframe,
      }),
    }),
  // S5-01 / SEC-03 : body StrategyTimeframeBody.
  toggleStrategyTimeframe: (args: {
    strategy: string;
    timeframe: string;
    enabled?: boolean;
    symbol?: string;
  }) =>
    apiFetch('/config/strategy-timeframe', {
      method: 'POST',
      body: JSON.stringify({
        strategy: args.strategy,
        timeframe: args.timeframe,
        enabled: args.enabled ?? true,
        symbol: args.symbol,
      }),
    }),
  // Legacy aliases (compat with existing code)
  updateStrategyParams: (payload: any) =>
    apiFetch('/config/strategy-params', { method: 'POST', body: JSON.stringify(payload) }),
  toggleStrategyTimeframeLegacy: (payload: any) =>
    apiFetch('/config/strategy-timeframe', { method: 'POST', body: JSON.stringify(payload) }),
  setStrategies: (enabled: string[]) =>
    apiFetch('/config/strategies', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),
  setTimeframes: (tfs: string[]) =>
    apiFetch('/config/timeframes', {
      method: 'POST',
      body: JSON.stringify({ timeframes: tfs }),
    }),

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
  /**
   * POST /api/backtest.
   *
   * ⚠ `run_backtest` (app/api/routes/backtest.py:47) déclare des paramètres
   * SCALAIRES — FastAPI les lit donc en **query string**, jamais dans le corps.
   * Cette méthode envoyait `JSON.stringify(payload)` en body : le backend
   * n'en lisait rien et retombait sur ses valeurs par défaut. Concrètement,
   * **toute la configuration de backtest de l'UI était ignorée** — symbole,
   * timeframe, limite, sélection de stratégies, et surtout `walk_forward` /
   * `monte_carlo`, qui restaient donc à `false` quoi que coche l'utilisateur.
   * C'est pour cette raison que les blocs Walk-Forward et Monte-Carlo ne
   * pouvaient de toute façon jamais s'afficher.
   *
   * Vérifié contre le backend : en body → 0 filtre appliqué et aucun WF/MC ;
   * en query string → `by_strategy` réduit à la stratégie demandée, `wf` et
   * `mc` présents.
   *
   * `strategy` (singulier) est l'ancien nom passé par /compare et l'ancienne
   * page /backtest ; le backend n'accepte que `strategies` (CSV). On normalise
   * ici plutôt que de laisser ces pages filtrer dans le vide.
   */
  runBacktest: (params: {
    symbol?: string; timeframe?: string; limit?: number;
    walk_forward?: boolean; monte_carlo?: boolean; dual_pass?: boolean;
    strategies?: string; strategy?: string;
  }) => {
    const { strategy, ...rest } = params;
    const merged: Record<string, unknown> = { ...rest };
    if (merged.strategies === undefined && strategy !== undefined) {
      merged.strategies = strategy;
    }
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(merged)) {
      if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
    }
    return apiFetch<BacktestResult | BacktestResult[]>(`/backtest?${q.toString()}`, {
      method: 'POST',
      timeoutMs: 0,
      // Verrouille `by_strategy` (dictionnaire), `equity_curve` (tableau de
      // nombres) et surtout les sous-objets `walk_forward` / `monte_carlo`,
      // dont `/lab` lisait des champs inexistants.
      schema: BacktestSchema,
    });
  },
  // S5-F3-US1 — Cancel backtest
  cancelBacktest: () => apiFetch<{ status: string }>('/backtest/cancel', { method: 'POST' }),

  // ── Scanner ─────────────────────────────────────────────────────────────
  fastAnalysis: (symbol: string, tf = '1h') =>
    // La route backend est `/api/scanner/fast_analysis` (underscore, cf.
    // app/api/routes/scanner.py:23). Le tiret utilisé ici renvoyait un 404 :
    // le bouton « Analyse rapide » de /scanner ne fonctionnait pas.
    apiFetch<any>(`/scanner/fast_analysis?symbol=${encodeURIComponent(symbol)}&tf=${tf}`, { timeoutMs: 0 }),

  // S8-F4-US1 — Scanner multi-symboles (peut être long sous Docker)
  scanMarket: (timeframe = '1h', limit = 50) =>
    apiFetch<any>(`/scanner?timeframe=${timeframe}&limit=${limit}`, { timeoutMs: 0 }),
  // S8-F4-US2 — Top opportunités
  getOpportunities: (timeframe = '1h', limit = 10) =>
    apiFetch<any>(
      `/scanner/opportunities?timeframe=${timeframe}&limit=${limit}`,
      { timeoutMs: 90_000 },
    ),
  /** Signaux SMC récents (&lt; 5 j) — job background data/smc_signals_recent.json */
  getSmcSignalsRecent: (refresh = false) =>
    apiFetch<any>(
      `/scanner/smc_signals_recent?refresh=${refresh ? 'true' : 'false'}`,
      { timeoutMs: refresh ? 180_000 : 15_000 },
    ),
  // S8-F4-US3 — Setups V11/V12 markers
  getSetupSeries: (symbol: string, timeframe = '1h', limit = 500, strategy = 'v11') =>
    apiFetch<any>(`/scanner/setup_series?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}&strategy=${strategy}`),
  // S8-F4-US4 — Signaux récents : déjà exposé plus bas dans la section
  // « SMC / Scanner » (`getSignals`). Le redéclarer ici créait une clé dupliquée
  // dans le littéral d'objet — la seconde définition écrasait celle-ci en
  // silence et `tsc` échouait (TS1117).
  // S8-F4-US5 — Config scanner
  getScannerConfig: () => apiFetch<any>('/scanner/config', { schema: ScannerConfigSchema }),
  // Chart multi-panneaux (OHLCV + EMA/BB/RSI/MACD/volume)
  getScannerChart: (symbol: string, timeframe = '1h', limit = 300) =>
    apiFetch<any>(
      `/scanner/chart?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`,
      { timeoutMs: 60_000 },
    ),

  // ── Univers ─────────────────────────────────────────────────────────────
  // S8-F2-US1 — Liste des univers
  getUniverses: () => apiFetch<any>('/universe', { schema: UniversesSchema }),
  // S8-F2-US2 — Membres d'un univers
  getUniverse: (name: string, opts?: { includeBars?: boolean }) =>
    apiFetch<any>(
      `/universe/${encodeURIComponent(name)}${opts?.includeBars ? '?include_bars=true' : ''}`,
    ),
  // S8-F2-US3 — Ajouter/retirer symbole
  addUniverseSymbol: (universe: string, body: { symbol: string; name?: string; provider_symbol?: string }) =>
    apiFetch<any>(`/universe/${encodeURIComponent(universe)}/symbols`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  removeUniverseSymbol: (universe: string, symbol: string) =>
    apiFetch<any>(`/universe/${encodeURIComponent(universe)}/symbols/${encodeURIComponent(symbol)}`, {
      method: 'DELETE',
    }),

  // ── Data ────────────────────────────────────────────────────────────────
  // Timeout élargi : inventaire multi-milliers de Parquet sous Docker/Windows.
  getDataStatus: () => apiFetch<any>('/data/status', { timeoutMs: 60_000 }),
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
    apiFetch<any>(`/optimize/status${jobId ? `?job_id=${jobId}` : ''}`, { schema: OptimizeStatusSchema }),
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
  getOptimizeResults: () => apiFetch<any>('/optimize/results', { schema: OptimizeResultsSchema }),
  getOptimizeSpaces: () => apiFetch<any>('/optimize/spaces', { schema: OptimizeSpacesSchema }),
  // Same-origin comme le reste : passe par le proxy, donc authentifié.
  optimizeStreamUrl: (jobId: string) => `${API_BASE}/optimize/stream?job_id=${jobId}`,

  // ── ML ──────────────────────────────────────────────────────────────────
  getMLStrategyInfo: () => apiFetch<{ strategies: Record<string, any> }>('/ml/strategy-info'),
  getCandlesStats: () => apiFetch<{ store: any }>('/candles/stats'),
  // S9-F3-US1 — Recettes ML disponibles
  getMLRecipes: () => apiFetch<{ recipes: any[] }>('/ml/recipes', { schema: MlRecipesResponseSchema }),

  // ── Config (S9-F3-US3 changelog optimizer + S9-F3-US4 test notif) ──────
  getConfigChangelog: (limit = 100) => apiFetch<any>(`/config/changelog?limit=${limit}`),
  testNotifications: () => apiFetch<{ status: string }>('/config/notifications/test', { method: 'POST' }),

  // ── ML Model Registry (ML-02) ────────────────────────────────────────────
  getMLRegistry: () => apiFetch<{ models: ModelRegistryEntry[] }>('/ml/registry', { schema: MlRegistrySchema }),
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
    apiFetch<any>(`/derivatives/data?symbol=${encodeURIComponent(symbol)}&period=${period}&limit=${limit}&refresh=${refresh}`, { schema: DerivativesDataSchema }),
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
    apiFetch<any>(
      `/scanner/smc?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`,
      { schema: SmcSchema, timeoutMs: 60_000 },
    ),
  getSMCReplay: (symbol = 'BTC/USDC', timeframe = '4h', limit = 800) =>
    apiFetch<any>(
      `/scanner/smc_replay?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`,
      { schema: SmcSchema, timeoutMs: 120_000 },
    ),
  getSignals: (symbol = 'BTC/USDC', timeframe = '1h', limit = 300) =>
    apiFetch<any>(`/scanner/signals?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`, { schema: ScannerSignalsSchema }),
};
