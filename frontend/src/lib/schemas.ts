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

export type BotStatusOut = z.infer<typeof BotStatusSchema>;
export type BotsResponseOut = z.infer<typeof BotsResponseSchema>;
export type OosTrackerOut = z.infer<typeof OosTrackerSchema>;
export type MlRecipesResponseOut = z.infer<typeof MlRecipesResponseSchema>;
