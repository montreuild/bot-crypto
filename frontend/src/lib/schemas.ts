/**
 * Schémas zod des réponses d'API.
 *
 * ── Pourquoi ce fichier existe ────────────────────────────────────────────
 * `api.ts` typait toutes ses réponses en `any`. Conséquence directe : trois
 * composants de la refonte S0-S9 ont été écrits contre une forme de payload
 * imaginée, sans que `tsc` ni `next build` ne bronchent, et ne se sont révélés
 * qu'à l'exécution face au backend :
 *
 *   - `/api/oos-tracker` renvoie `slots` en **dictionnaire** indexé par
 *     slot_key ; `/bots-v2` faisait `slots.find(...)` → la page tombait dans
 *     l'ErrorBoundary à l'ouverture d'un bot.
 *   - `/api/ml/recipes` renvoie `features_catalog` en **chaîne** (identifiant
 *     de catalogue) ; `MLRecipesList` faisait `.slice().map()` dessus → la page
 *     `/ml` plantait au chargement.
 *   - `MonteCarloCone` attendait des séries temporelles que l'API n'expose pas.
 *
 * ── Principe retenu ───────────────────────────────────────────────────────
 * Les schémas sont **permissifs par défaut** (`.passthrough()`, champs
 * optionnels) : le but n'est pas de rejeter les réponses du backend, mais de
 * garantir la **forme des champs que l'UI manipule** — un tableau reste un
 * tableau, un dictionnaire reste un dictionnaire. En cas d'écart, `safeParse`
 * laisse passer la donnée brute et journalise : on préfère une UI dégradée à
 * une UI qui plante.
 *
 * Ne sont couverts que les endpoints réellement consommés par les pages v2.
 * Étendre au fil des besoins plutôt que d'un bloc.
 */

import { z } from 'zod';

// ── Primitives tolérantes ───────────────────────────────────────────────────

/** Nombre qui accepte `null` (le backend renvoie souvent `null` pour « pas de donnée »). */
const num = z.number().nullish();

// ── /api/status ─────────────────────────────────────────────────────────────

/**
 * Tant que le trader n'est pas démarré, la réponse se réduit à
 * `{status: "not_started"}` : **tous** les autres champs sont absents. C'est ce
 * qui a fait afficher « Mode live » par `HealthBanner` (`paper_mode` undefined
 * testé sans repli). Ils sont donc tous optionnels, volontairement.
 */
export const BotStatusSchema = z
  .object({
    status: z.string(),
    paper_mode: z.boolean().optional(),
    total_pnl: num,
    total_pnl_pct: num,
    circuit_breaker_active: z.boolean().optional(),
    circuit_breaker_reason: z.string().nullish(),
  })
  .passthrough();

// ── /api/bots ───────────────────────────────────────────────────────────────

export const BotSchema = z
  .object({
    slot_key: z.string(),
    strategy: z.string().optional(),
    timeframe: z.string().optional(),
    symbol: z.string().nullish(),
    state: z.string().optional(),
    /**
     * `true` = slot **forcé en actif** via `lifecycle.manual_active`
     * (cf. app/api/routes/portfolio.py:152). `false` est l'état normal d'un bot
     * piloté par le cycle de vie automatique — ce n'est PAS un « bot gelé ».
     */
    manual_active: z.boolean().optional(),
    edge: z
      .object({
        available: z.boolean().optional(),
        n: num,
        ci_low_pct: num,
        ci_high_pct: num,
        worst_trade_pct: num,
      })
      .passthrough()
      .nullish(),
  })
  .passthrough();

export const BotsResponseSchema = z
  .object({
    bots: z.array(BotSchema).default([]),
    counts: z.record(z.string(), z.number()).default({}),
  })
  .passthrough();

// ── /api/oos-tracker ────────────────────────────────────────────────────────

export const OosSlotSchema = z
  .object({
    slot_key: z.string().optional(),
    monte_carlo: z
      .object({
        runs: num,
        return_p5_pct: num,
        return_mean_pct: num,
        return_p95_pct: num,
        max_dd_p95_pct: num,
        prob_profit: num,
      })
      .passthrough()
      .optional(),
    live: z.object({ n_trades: num, avg_return_pct: num }).passthrough().optional(),
    contract: z
      .object({
        available: z.boolean().optional(),
        live_mean_pct: num,
        in_band: z.boolean().nullish(),
        verdict: z.string().optional(),
      })
      .passthrough()
      .optional(),
  })
  .passthrough();

/**
 * ⚠ `slots` est un **dictionnaire** indexé par slot_key, pas un tableau.
 * C'est précisément l'erreur qui faisait planter `/bots-v2`.
 */
export const OosTrackerSchema = z
  .object({ slots: z.record(z.string(), OosSlotSchema).default({}) })
  .passthrough();

// ── /api/ml/recipes ─────────────────────────────────────────────────────────

export const MlRecipeSchema = z
  .object({
    recipe: z.string(),
    trainable: z.boolean().optional(),
    reason: z.string().nullish(),
    /** ⚠ Identifiant de catalogue (« dyn_threshold@1 »), PAS une liste de features. */
    features_catalog: z.string().nullish(),
    label_scheme: z.string().nullish(),
    heads: z.array(z.string()).default([]),
  })
  .passthrough();

export const MlRecipesResponseSchema = z
  .object({ recipes: z.array(MlRecipeSchema).default([]) })
  .passthrough();

// ── /api/stats/* ────────────────────────────────────────────────────────────

export const DailyStatsSchema = z.array(
  z.object({ date: z.string().optional(), pnl: num, equity: num }).passthrough(),
);

export const FeesBreakdownSchema = z
  .object({ taker: num, maker: num, borrow: num, stop: num })
  .passthrough();

// ── /health (racine, hors préfixe /api) ─────────────────────────────────────

export const HealthSchema = z
  .object({
    status: z.string(),
    db: z.boolean().optional(),
    exchange: z.boolean().optional(),
    trader: z.boolean().optional(),
  })
  .passthrough();

// ── /api/universe ───────────────────────────────────────────────────────────

export const UniversesSchema = z
  .object({
    universes: z
      .array(
        z
          .object({
            id: z.string(),
            label: z.string().optional(),
            venue: z.string().optional(),
            asset_class: z.string().optional(),
            quote_currency: z.string().optional(),
            n_symbols: z.number().optional(),
            verified: z.boolean().optional(),
          })
          .passthrough(),
      )
      .default([]),
  })
  .passthrough();

// ── /api/optimize/* ─────────────────────────────────────────────────────────

/**
 * ⚠ Sans `job_id`, `/api/optimize/status` renvoie `get_all_jobs()`, soit un
 * **dictionnaire indexé par job_id** (app/engine/auto_optimizer.py:208) — même
 * piège que `oos-tracker`. Avec `job_id`, c'est un job unique.
 * Les deux formes passent par le même appel côté front, d'où l'union.
 */
export const OptimizeJobSchema = z
  .object({
    job_id: z.string().optional(),
    status: z.string().optional(),
    progress: z.unknown().optional(),
    error: z.string().nullish(),
  })
  .passthrough();

export const OptimizeStatusSchema = z.union([
  z.record(z.string(), OptimizeJobSchema),
  OptimizeJobSchema,
]);

/**
 * ⚠ Dictionnaire **à deux niveaux** : stratégie → timeframe → entrée
 * (app/api/routes/optimizer.py:415). Ni l'un ni l'autre n'est un tableau.
 */
export const OptimizeResultsSchema = z.record(
  z.string(),
  z.record(z.string(), z.unknown()),
);

/** ⚠ Dictionnaire indexé par nom de stratégie. */
export const OptimizeSpacesSchema = z.record(
  z.string(),
  z
    .object({
      params: z.unknown().optional(),
      timeframes: z.array(z.string()).default([]),
      n_combos: num,
      is_ml: z.boolean().optional(),
    })
    .passthrough(),
);

// ── /api/ml/registry ────────────────────────────────────────────────────────

export const MlRegistrySchema = z
  .object({
    models: z
      .array(
        z
          .object({
            tf: z.string().optional(),
            recipe: z.string().optional(),
            train_symbol: z.string().nullish(),
            n_versions: num,
            active: z.unknown().nullish(),
            pinned_version_id: z.string().nullish(),
            freshness_warning: z.unknown().nullish(),
          })
          .passthrough(),
      )
      .default([]),
  })
  .passthrough();

// ── /api/derivatives/* ──────────────────────────────────────────────────────

/**
 * ⚠ `_series_payload` (app/api/routes/derivatives.py:35) renvoie **`null`**
 * quand la série est vide. Chaque entrée de `metrics` est donc nullable : la
 * parcourir sans garde plante sur `.time.length`.
 */
export const DerivativesSeriesSchema = z
  .object({
    time: z.array(z.number()).default([]),
    value: z.array(z.number().nullable()).default([]),
    count: num,
    first: z.string().optional(),
    last: z.string().optional(),
  })
  .passthrough()
  .nullable();

export const DerivativesDataSchema = z
  .object({
    symbol: z.string().optional(),
    period: z.string().optional(),
    metrics: z.record(z.string(), DerivativesSeriesSchema).default({}),
    price: z
      .object({ time: z.array(z.number()), close: z.array(z.number()) })
      .passthrough()
      .optional(),
  })
  .passthrough();

// ── /api/scanner/* (SMC) ────────────────────────────────────────────────────

/**
 * Un plan de trade de `Strategy.trade_plans()`, exposé par `/api/scanner/smc`
 * sous la clé `trade_plans`.
 *
 * `score_min` (et non `score`) : les confluences liées à la bougie de
 * déclenchement ne sont pas connues d'avance, le backend n'expose donc qu'un
 * score plancher. La table lit aussi `status`, `gain_pct` et `distance_pct`,
 * absents de la version initiale de l'UI.
 */
export const TradePlanSchema = z
  .object({
    status: z.string().optional(),
    side: z.string().optional(),
    setup: z.string().nullish(),
    score_min: num,
    entry: num,
    stop: num,
    tp: num,
    gain_pct: num,
    rr: num,
    distance_pct: num,
    trigger: z.string().nullish(),
    reason: z.string().nullish(),
    /**
     * Epoch **secondes** (même unité que `time_start`/`time_end` des order
     * blocks), et non millisecondes — le formatage doit en tenir compte.
     * `null` si la fenêtre analysée ne porte pas de colonne `time`.
     */
    signal_time: num,
  })
  .passthrough();

/**
 * Les payloads SMC sont volumineux et très variables selon les overlays
 * activés. On ne verrouille que l'enveloppe : le détail reste `passthrough`,
 * sinon le schéma deviendrait plus fragile que le code qu'il protège.
 */
export const SmcSchema = z
  .object({
    symbol: z.string().optional(),
    timeframe: z.string().optional(),
    candles: z.array(z.unknown()).optional(),
    // `trade_plans` doit rester un tableau : la table « Plans recommandés » le
    // trie et le mappe directement.
    trade_plans: z.array(TradePlanSchema).optional(),
  })
  .passthrough();

/**
 * `/api/scanner/signals` — un signal par stratégie découverte.
 *
 * Le panel « Prédictions par stratégie » lit `p_event` / `p_up` (fractions
 * 0-1, pas des pourcentages) et `skipped` (stratégie ML sans modèle entraîné).
 * `signals` doit rester un **tableau**.
 */
export const StrategySignalSchema = z
  .object({
    strategy: z.string().optional(),
    side: z.string().optional(),
    score: num,
    reason: z.string().nullish(),
    skipped: z.boolean().optional(),
    active: z.boolean().optional(),
    setup: z.string().nullish(),
    regime_lbl: z.string().nullish(),
    p_event: num,
    p_up: num,
  })
  .passthrough();

export const ScannerSignalsSchema = z
  .object({ signals: z.array(StrategySignalSchema).optional() })
  .passthrough();

export const ScannerConfigSchema = z.object({}).passthrough();

// ── /api/backtest ───────────────────────────────────────────────────────────

/**
 * Walk-Forward — sortie de `WalkForwardAnalyzer.run()`.
 *
 * ⚠ Contrat qui a réellement dérivé : `/lab` lisait `walk_forward.folds`
 * (tableau) et `walk_forward.oos_pnl`. **Aucun de ces deux champs n'existe.**
 * Le backend renvoie `out_of_sample` / `in_sample` (tableaux de
 * `BacktestResult.to_dict()`) et `avg_oos_pnl`. Le résumé affichait donc
 * « 0 folds · OOS PnL: $0.00 » quel que soit le résultat.
 *
 * `n_folds` et `out_of_sample` sont **requis** dans la branche succès : c'est
 * ce qui fait échouer la forme imaginée et rend le test négatif utile. La
 * validation reste non bloquante côté `apiFetch` (log + donnée brute).
 */
export const WalkForwardErrorSchema = z
  .object({ error: z.string() })
  .passthrough();

export const WalkForwardSuccessSchema = z
  .object({
    n_folds: z.number(),
    avg_oos_pnl: num,
    avg_oos_sharpe: num,
    avg_oos_wr: num,
    consistency: num,
    in_sample: z.array(z.unknown()).optional(),
    out_of_sample: z.array(z.unknown()),
  })
  .passthrough();

export const WalkForwardSchema = z.union([
  WalkForwardErrorSchema,
  WalkForwardSuccessSchema,
]);

/**
 * Monte-Carlo — sortie de `MonteCarlo.run()`.
 *
 * ⚠ `/lab` lisait `p5` / `p50` / `p95` : ces champs n'existent pas. Le backend
 * expose `final_equity_p5` / `final_equity_mean` / `final_equity_p95`, plus
 * `prob_profit`, `prob_ruin_10pct` et `max_dd_p95`.
 *
 * Il n'y a **aucune série temporelle** : uniquement des agrégats scalaires sur
 * l'équité finale. Tout composant qui voudrait tracer des courbes P5/P50/P95
 * dans le temps se retrouverait vide — c'est exactement l'erreur commise sur
 * `MonteCarloCone` en S4 (R17).
 */
export const MonteCarloErrorSchema = z
  .object({ error: z.string() })
  .passthrough();

export const MonteCarloSuccessSchema = z
  .object({
    runs: z.number(),
    confidence: num,
    final_equity_mean: z.number(),
    final_equity_p5: z.number(),
    final_equity_p95: z.number(),
    max_dd_p95: num,
    prob_profit: num,
    prob_ruin_10pct: num,
  })
  .passthrough();

export const MonteCarloSchema = z.union([
  MonteCarloErrorSchema,
  MonteCarloSuccessSchema,
]);

/**
 * Un trade de backtest. `entry_time` est `str(df["time"][i])` côté backend :
 * selon la source c'est un ISO ou un epoch sérialisé, d'où `z.union`.
 */
export const BacktestTradeSchema = z
  .object({
    side: z.string().optional(),
    strategy: z.string().optional(),
    entry: num,
    exit: num,
    entry_time: z.union([z.string(), z.number()]).nullish(),
    exit_time: z.union([z.string(), z.number()]).nullish(),
    bar: num,
    pnl: num,
    pnl_pct: num,
    exit_reason: z.string().nullish(),
  })
  .passthrough();

/** Entrée de `by_strategy` — `equity_curve` doit rester un tableau de nombres. */
export const BacktestStrategySchema = z
  .object({
    total_trades: num,
    win_rate: num,
    total_pnl: num,
    sharpe: num,
    max_drawdown: num,
    profit_factor: num,
    initial_capital: num,
    final_equity: num,
    equity_curve: z.array(z.number()).optional(),
    buy_and_hold_pnl: num,
    alpha: num,
    trades: z.array(BacktestTradeSchema).optional(),
    walk_forward: WalkForwardSchema.optional(),
    monte_carlo: MonteCarloSchema.optional(),
  })
  .passthrough();

/** `by_strategy` est un **dictionnaire** indexé par nom de stratégie. */
export const BacktestSchema = z
  .object({
    symbol: z.string().optional(),
    timeframe: z.string().optional(),
    by_strategy: z.record(z.string(), BacktestStrategySchema).optional(),
  })
  .passthrough();

// ── /api/replay ─────────────────────────────────────────────────────────────

export const ReplaySchema = z.object({}).passthrough();

export type BotStatusOut = z.infer<typeof BotStatusSchema>;
export type BotsResponseOut = z.infer<typeof BotsResponseSchema>;
export type OosTrackerOut = z.infer<typeof OosTrackerSchema>;
export type MlRecipesResponseOut = z.infer<typeof MlRecipesResponseSchema>;
