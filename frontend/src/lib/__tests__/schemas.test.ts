/**
 * Tests de non-régression sur les contrats d'API.
 *
 * Chaque bloc verrouille une forme de payload dont l'écart a **réellement**
 * cassé une page pendant la revue d'intégration S0-S9. Les payloads d'exemple
 * sont copiés des réponses du backend, pas inventés.
 */

import { describe, it, expect } from 'vitest';
import {
  BotStatusSchema,
  BotsResponseSchema,
  OosTrackerSchema,
  MlRecipesResponseSchema,
  FeesBreakdownSchema,
  HealthSchema,
  OptimizeStatusSchema,
  OptimizeResultsSchema,
  OptimizeSpacesSchema,
  DerivativesDataSchema,
  MlRegistrySchema,
} from '@/lib/schemas';

describe('BotStatus', () => {
  it('accepte la réponse minimale du trader non démarré', () => {
    // C'est tout ce que renvoie /api/status tant que le bot n'est pas lancé.
    const parsed = BotStatusSchema.safeParse({ status: 'not_started' });
    expect(parsed.success).toBe(true);
  });

  it('laisse paper_mode undefined quand le champ est absent', () => {
    // HealthBanner testait `status.paper_mode` sans repli et affichait donc
    // « Mode live » sur un bot à l'arrêt. Le repli `?? true` est côté composant,
    // mais le schéma ne doit surtout pas inventer une valeur par défaut.
    const parsed = BotStatusSchema.parse({ status: 'not_started' });
    expect(parsed.paper_mode).toBeUndefined();
  });
});

describe('Bots', () => {
  it('accepte un slot candidat sans override manuel', () => {
    const parsed = BotsResponseSchema.safeParse({
      bots: [{ slot_key: 'breakout::15m', state: 'candidat', manual_active: false }],
      counts: { candidat: 240, essai: 0, actif: 0, retire: 0 },
    });
    expect(parsed.success).toBe(true);
  });

  it('manual_active: false signifie « pas de forçage », pas « bot gelé »', () => {
    // Le filtre de /bots-v2 lisait `false` comme « gelé » et masquait les 240
    // candidats : le kanban s'affichait vide. Ce test documente la sémantique.
    const parsed = BotsResponseSchema.parse({
      bots: [{ slot_key: 'a::1h', manual_active: false }, { slot_key: 'b::1h', manual_active: true }],
      counts: {},
    });
    const forces = parsed.bots.filter((b) => b.manual_active === true);
    expect(forces).toHaveLength(1);
    expect(forces[0].slot_key).toBe('b::1h');
  });
});

describe('OosTracker', () => {
  it('slots est un dictionnaire indexé par slot_key, pas un tableau', () => {
    // /bots-v2 faisait `slots.find(...)` → « oosData.slots.find is not a
    // function » et toute la page tombait dans l'ErrorBoundary.
    const parsed = OosTrackerSchema.parse({
      slots: { 'breakout::1h': { slot_key: 'breakout::1h' } },
    });
    expect(Array.isArray(parsed.slots)).toBe(false);
    expect(parsed.slots['breakout::1h'].slot_key).toBe('breakout::1h');
  });

  it('rejette la forme tableau qui avait été supposée', () => {
    const parsed = OosTrackerSchema.safeParse({ slots: [{ slot_key: 'breakout::1h' }] });
    expect(parsed.success).toBe(false);
  });

  it('expose des agrégats scalaires, pas des séries temporelles', () => {
    // MonteCarloCone attendait labels/median/ci_lower/ci_upper en tableaux :
    // `chartData` était toujours vide et le cône affichait « pas de données »
    // pour tous les bots.
    const slot = OosTrackerSchema.parse({
      slots: {
        's::1h': {
          monte_carlo: { runs: 200, return_p5_pct: 1.648, return_mean_pct: 1.648, return_p95_pct: 1.648, prob_profit: 100 },
          live: { n_trades: 0, avg_return_pct: null },
          contract: { available: false, live_mean_pct: null, in_band: null, verdict: 'pas_assez_de_trades_reels' },
        },
      },
    }).slots['s::1h'];
    expect(typeof slot.monte_carlo?.return_p5_pct).toBe('number');
    expect(slot.contract?.verdict).toBe('pas_assez_de_trades_reels');
  });
});

describe('MlRecipes', () => {
  it('features_catalog est une chaîne (identifiant de catalogue)', () => {
    // MLRecipesList faisait `.slice().map()` dessus → /ml plantait, et avant ça
    // le badge affichait la longueur de la chaîne (« 15 features »).
    const parsed = MlRecipesResponseSchema.parse({
      recipes: [{
        recipe: 'dyn_threshold_v1',
        trainable: true,
        reason: null,
        features_catalog: 'dyn_threshold@1',
        label_scheme: 'vol_adaptive_dir',
        heads: ['dir'],
      }],
    });
    expect(typeof parsed.recipes[0].features_catalog).toBe('string');
    expect(Array.isArray(parsed.recipes[0].heads)).toBe(true);
  });

  it('rejette features_catalog sous forme de tableau', () => {
    const parsed = MlRecipesResponseSchema.safeParse({
      recipes: [{ recipe: 'x', features_catalog: ['rsi', 'atr'] }],
    });
    expect(parsed.success).toBe(false);
  });
});

describe('Optimize', () => {
  it('status sans job_id est un dictionnaire indexé par job_id', () => {
    // `get_all_jobs()` (auto_optimizer.py:208) renvoie un dict — même piège que
    // oos-tracker. Le confondre avec un tableau casserait la page /optimizer.
    const parsed = OptimizeStatusSchema.parse({ ab12cd: { job_id: 'ab12cd', status: 'running' } });
    expect(Array.isArray(parsed)).toBe(false);
  });

  it('status avec job_id est un job unique', () => {
    expect(OptimizeStatusSchema.safeParse({ job_id: 'ab12cd', status: 'done' }).success).toBe(true);
  });

  it('results est un dictionnaire à DEUX niveaux : stratégie -> timeframe', () => {
    const parsed = OptimizeResultsSchema.parse({ trend_rider: { '1h': { score: 1.2 }, '4h': { score: 0.8 } } });
    expect(Object.keys(parsed.trend_rider)).toEqual(['1h', '4h']);
  });

  it('spaces est indexé par nom de stratégie', () => {
    const parsed = OptimizeSpacesSchema.parse({
      breakout: { params: {}, timeframes: ['1h', '4h', '1d'], n_combos: 240, is_ml: false },
    });
    expect(parsed.breakout.timeframes).toHaveLength(3);
  });
});

describe('Derivatives', () => {
  it('une métrique sans données vaut null, pas un tableau vide', () => {
    // `_series_payload` (derivatives.py:35) renvoie `None` si la série est vide.
    // Itérer dessus sans garde plante sur `.time.length`.
    const parsed = DerivativesDataSchema.parse({
      symbol: 'BTC/USDC', period: '1h',
      metrics: { funding: null, open_interest: { time: [1], value: [2.5], count: 1 } },
    });
    expect(parsed.metrics.funding).toBeNull();
    expect(parsed.metrics.open_interest?.value).toEqual([2.5]);
  });

  it('accepte des valeurs nulles dans une série', () => {
    expect(
      DerivativesDataSchema.safeParse({ metrics: { x: { time: [1, 2], value: [1.0, null], count: 2 } } }).success,
    ).toBe(true);
  });
});

describe('MlRegistry', () => {
  it('accepte un registre vide et un modèle sans version active', () => {
    expect(MlRegistrySchema.parse({ models: [] }).models).toEqual([]);
    const p = MlRegistrySchema.parse({
      models: [{ tf: '1h', recipe: 'v4_polars', n_versions: 3, active: null, pinned_version_id: null }],
    });
    expect(p.models[0].active).toBeNull();
  });
});

describe('Divers', () => {
  it('FeesBreakdown accepte la réponse à zéro', () => {
    expect(FeesBreakdownSchema.safeParse({ taker: 0, maker: 0, borrow: 0, stop: 0 }).success).toBe(true);
  });

  it('Health accepte le statut dégradé du backend seul', () => {
    expect(
      HealthSchema.safeParse({ status: 'degraded', db: false, exchange: false, trader: false }).success,
    ).toBe(true);
  });
});
