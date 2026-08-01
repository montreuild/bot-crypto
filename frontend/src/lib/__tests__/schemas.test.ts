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
  isForcedActive,
  OosTrackerSchema,
  MlRecipesResponseSchema,
  FeesBreakdownSchema,
  HealthSchema,
  OptimizeStatusSchema,
  OptimizeResultsSchema,
  OptimizeSpacesSchema,
  DerivativesDataSchema,
  MlRegistrySchema,
  WalkForwardSchema,
  MonteCarloSchema,
  BacktestSchema,
  SmcSchema,
  ScannerSignalsSchema,
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

  it('force_active: false signifie « pas de forçage », pas « bot gelé »', () => {
    // Le filtre de /bots lisait `false` comme « gelé » et masquait les 240
    // candidats : le kanban s'affichait vide. Ce test documente la sémantique.
    const parsed = BotsResponseSchema.parse({
      bots: [{ slot_key: 'a::1h', force_active: false }, { slot_key: 'b::1h', force_active: true }],
      counts: {},
    });
    const forces = parsed.bots.filter((b) => isForcedActive(b));
    expect(forces).toHaveLength(1);
    expect(forces[0].slot_key).toBe('b::1h');
  });

  it('lit encore l\'ancien nom manual_active (D6, migration en cours)', () => {
    // L'API émet les deux clés le temps que les clients migrent : un bot forcé
    // ne doit pas repasser « non forcé » selon le nom utilisé par le backend.
    const parsed = BotsResponseSchema.parse({
      bots: [{ slot_key: 'a::1h', manual_active: true }],
      counts: {},
    });
    expect(isForcedActive(parsed.bots[0])).toBe(true);
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

// ── Backtest : Walk-Forward / Monte-Carlo ───────────────────────────────────
//
// Ces deux contrats n'avaient jamais été vérifiés. `/lab` lisait des champs
// qui n'existent dans AUCUNE réponse du backend, si bien que le résumé
// affichait « 0 folds » et « P5: $0.00 · P50: $0.00 · P95: $0.00 » quel que
// soit le résultat — sans que `tsc`, `next build` ni l'exécution ne signalent
// quoi que ce soit (pas de crash : juste des zéros crédibles).

describe('WalkForward', () => {
  // Payload calqué sur WalkForwardAnalyzer.run() (app/engine/walk_forward.py).
  const real = {
    n_folds: 4,
    avg_oos_pnl: 128.4312,
    avg_oos_sharpe: 1.21,
    avg_oos_wr: 54.3,
    consistency: 75.0,
    in_sample: [{ total_pnl: 900.1, win_rate: 61.2, sharpe: 2.1, total_trades: 42, max_drawdown: -8.4 }],
    out_of_sample: [{ total_pnl: 128.4, win_rate: 54.3, sharpe: 1.21, total_trades: 11, max_drawdown: -12.7 }],
  };

  it('accepte la réponse réelle', () => {
    const parsed = WalkForwardSchema.safeParse(real);
    expect(parsed.success).toBe(true);
  });

  it('rejette la forme supposée à tort (`folds` / `oos_pnl`)', () => {
    // C'est exactement ce que /lab lisait.
    expect(WalkForwardSchema.safeParse({ folds: [{}, {}], oos_pnl: 128.4 }).success).toBe(false);
  });

  it('accepte la branche erreur (données insuffisantes)', () => {
    expect(
      WalkForwardSchema.safeParse({
        error: 'IS trop court pour les stratégies EMA (120 barres/fold · min 200 requis)',
        n_bars: 600, fold_n: 120, min_required: 1200,
      }).success,
    ).toBe(true);
  });

  it('expose les folds OOS sous `out_of_sample`, jamais sous `folds`', () => {
    const parsed = WalkForwardSchema.parse(real) as typeof real;
    expect(parsed.out_of_sample).toHaveLength(1);
    expect((parsed as Record<string, unknown>).folds).toBeUndefined();
  });
});

describe('MonteCarlo', () => {
  // Payload calqué sur MonteCarlo.run() (app/engine/monte_carlo.py).
  const real = {
    runs: 200,
    confidence: 0.95,
    final_equity_mean: 10842.31,
    final_equity_p5: 9231.04,
    final_equity_p95: 12488.9,
    max_dd_p95: 18.42,
    prob_profit: 71.5,
    prob_ruin_10pct: 12.0,
  };

  it('accepte la réponse réelle', () => {
    expect(MonteCarloSchema.safeParse(real).success).toBe(true);
  });

  it('rejette la forme supposée à tort (`p5` / `p50` / `p95`)', () => {
    // C'est exactement ce que /lab lisait.
    expect(MonteCarloSchema.safeParse({ p5: 9231, p50: 10842, p95: 12488 }).success).toBe(false);
  });

  it('n’expose que des agrégats scalaires — aucune série temporelle', () => {
    // Verrouille la leçon de R17 : tracer un cône P5/P50/P95 dans le temps
    // n'est pas possible avec ce contrat, il faudrait un nouvel endpoint.
    const parsed = MonteCarloSchema.parse(real) as Record<string, unknown>;
    for (const k of ['labels', 'median', 'ci_lower', 'ci_upper', 'equity_curves']) {
      expect(parsed[k]).toBeUndefined();
    }
    expect(typeof parsed.final_equity_p5).toBe('number');
  });

  it('accepte la branche erreur (aucun trade fermé)', () => {
    expect(MonteCarloSchema.safeParse({ error: 'Aucun trade fermé' }).success).toBe(true);
  });
});

describe('Backtest', () => {
  it('`by_strategy` est un dictionnaire, pas un tableau', () => {
    const ok = BacktestSchema.safeParse({
      symbol: 'BTC/USDC', timeframe: '1h',
      by_strategy: { trend_rider: { total_trades: 12, total_pnl: 340.2, equity_curve: [10000, 10120] } },
    });
    expect(ok.success).toBe(true);
    expect(BacktestSchema.safeParse({ by_strategy: [{ total_trades: 12 }] }).success).toBe(false);
  });

  it('`equity_curve` reste un tableau de nombres', () => {
    expect(
      BacktestSchema.safeParse({ by_strategy: { s: { equity_curve: ['10000'] } } }).success,
    ).toBe(false);
  });

  it('accepte un trade complet et son horodatage ISO ou epoch', () => {
    const mk = (t: string | number) => BacktestSchema.safeParse({
      by_strategy: { s: { trades: [{ side: 'long', entry: 42000.5, pnl: 12.4, entry_time: t }] } },
    }).success;
    expect(mk('2026-07-30T12:00:00Z')).toBe(true);
    expect(mk(1753876800000)).toBe(true);
  });
});

describe('TradePlans & Prédictions', () => {
  it('`trade_plans` porte bien status/gain_pct/distance_pct/score_min', () => {
    // Les 4 colonnes qui manquaient à la table de /smartgraph.
    const parsed = SmcSchema.parse({
      symbol: 'BTC/USDC', timeframe: '1h',
      trade_plans: [{
        status: 'pending', side: 'long', setup: 'ob_retest', score_min: 0.62,
        entry: 41800, stop: 41200, tp: 43000, gain_pct: 2.87, rr: 2.0,
        distance_pct: 0.45, trigger: 'clôture > 41800', reason: 'OB frais aligné',
      }],
    });
    const p = parsed.trade_plans![0];
    expect(p.status).toBe('pending');
    expect(p.score_min).toBe(0.62);
    expect(p.distance_pct).toBe(0.45);
  });

  it('`trade_plans` doit rester un tableau', () => {
    expect(SmcSchema.safeParse({ trade_plans: { 0: { side: 'long' } } }).success).toBe(false);
  });

  it('les probabilités des signaux sont des fractions nullables', () => {
    const parsed = ScannerSignalsSchema.parse({
      symbol: 'BTC/USDC', timeframe: '1h',
      signals: [
        { strategy: 'trend_rider', side: 'long', score: 0.71, p_event: 0.62, p_up: 0.58, active: true, skipped: false },
        { strategy: 'ml_v4', side: 'none', score: null, reason: 'Aucun modèle entraîné', skipped: true, active: false },
      ],
    });
    expect(parsed.signals).toHaveLength(2);
    expect(parsed.signals![1].skipped).toBe(true);
    expect(parsed.signals![1].score).toBeNull();
  });

  it('`signals` doit rester un tableau', () => {
    expect(ScannerSignalsSchema.safeParse({ signals: { a: {} } }).success).toBe(false);
  });
});
