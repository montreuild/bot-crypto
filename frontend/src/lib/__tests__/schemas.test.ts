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
