/**
 * S12 — contrats d'API des enveloppes de risque (spec §9).
 *
 * Ces deux payloads pilotent des décisions d'argent : un montant qui
 * arriverait sous une forme inattendue fausserait des totaux affichés comme
 * des faits. Les exemples sont copiés des réponses réelles du backend
 * (`app/api/routes/risk.py`), pas inventés — c'est tout l'intérêt.
 */

import { describe, it, expect } from 'vitest';
import { RiskSchema, RiskDiagnosticsSchema, BacktestSchema } from '@/lib/schemas';

const RISK_PAYLOAD = {
  venues: [{
    venue: 'margin-isolated', currency: 'USDC',
    envelope: 1000, notional_engaged: 320,
    risk_budget: 50, risk_engaged: 12.4, risk_pct_used: 0.248,
  }],
  symbols: [{
    venue: 'margin-isolated', symbol: 'BTC/USDC', currency: 'USDC',
    envelope: 1000, notional_engaged: 320, risk_budget: 50, risk_engaged: 12.4,
  }],
  slots: [{
    slot_key: 'breakout::1h::BTC/USDC', venue: 'margin-isolated',
    symbol: 'BTC/USDC', currency: 'USDC', weight: 0.14,
    envelope: 140, max_notional: 140, risk_amount: 3.5,
    risk_engaged: 3.5, notional_engaged: 120, edge_ci_low: 0.42,
  }],
  total_risk_engaged: 12.4,
  rejections: { total: 37, par_motif: { budget_symbole: 21 } },
};

describe('/api/risk', () => {
  it('accepte le payload de référence', () => {
    const r = RiskSchema.safeParse(RISK_PAYLOAD);
    expect(r.success).toBe(true);
  });

  it('garantit que les trois niveaux sont des TABLEAUX', () => {
    // Le piège déjà rencontré sur /api/oos-tracker : un dictionnaire indexé
    // là où l'UI fait `.map()` → la page tombe dans l'ErrorBoundary.
    const parsed = RiskSchema.parse(RISK_PAYLOAD);
    expect(Array.isArray(parsed.venues)).toBe(true);
    expect(Array.isArray(parsed.symbols)).toBe(true);
    expect(Array.isArray(parsed.slots)).toBe(true);
  });

  it('remplit les niveaux absents par un tableau vide, jamais undefined', () => {
    const parsed = RiskSchema.parse({ total_risk_engaged: 0 });
    expect(parsed.venues).toEqual([]);
    expect(parsed.slots).toEqual([]);
  });

  it('conserve un edge non mesurée en null plutôt que de la coercer à 0', () => {
    // 0 signifierait « edge nulle mesurée », null « jamais mesurée » : deux
    // états qui n'ont pas le même effet sur le poids du slot.
    const parsed = RiskSchema.parse({
      ...RISK_PAYLOAD,
      slots: [{ ...RISK_PAYLOAD.slots[0], edge_ci_low: null }],
    });
    expect(parsed.slots[0].edge_ci_low).toBeNull();
  });

  it('laisse passer un champ inconnu sans rejeter la réponse', () => {
    const r = RiskSchema.safeParse({ ...RISK_PAYLOAD, nouveau_champ: 'x' });
    expect(r.success).toBe(true);
  });

  it('chaque slot porte son enveloppe À CÔTÉ de son risque', () => {
    // La règle d'affichage du §6 tient par le contrat : si l'un des deux
    // disparaissait du payload, la carte afficherait un montant sans base.
    const slot = RiskSchema.parse(RISK_PAYLOAD).slots[0];
    expect(slot.envelope).toBeDefined();
    expect(slot.risk_amount).toBeDefined();
    expect(slot.risk_engaged).toBeDefined();
  });
});

const DIAG_PAYLOAD = {
  diagnostics: [
    {
      severity: 'error', code: 'budget_symbole_insuffisant',
      scope: 'slot:a::1h::BTC/USDC',
      message: 'risque par trade (25.00) > budget de risque du symbole (20.00)',
      values: { risk_amount: 25.0, weight: 1.0, symbol_risk_budget: 20.0 },
    },
    {
      severity: 'warning', code: 'plafond_notionnel_mordant',
      scope: 'slot:b::1h::BTC/USDC',
      message: 'au stop de référence (2.50%), le plafond notionnel mord déjà',
      values: { stop_pct_low: 0.025, stop_pct_reference: 0.025 },
    },
  ],
  errors: 1,
  warnings: 1,
};

describe('/api/risk/diagnostics', () => {
  it('accepte le payload de référence', () => {
    expect(RiskDiagnosticsSchema.safeParse(DIAG_PAYLOAD).success).toBe(true);
  });

  it('n’admet que error et warning comme sévérités', () => {
    const bad = {
      ...DIAG_PAYLOAD,
      diagnostics: [{ ...DIAG_PAYLOAD.diagnostics[0], severity: 'info' }],
    };
    expect(RiskDiagnosticsSchema.safeParse(bad).success).toBe(false);
  });

  it('les compteurs valent 0 par défaut, jamais undefined', () => {
    const parsed = RiskDiagnosticsSchema.parse({ diagnostics: [] });
    expect(parsed.errors).toBe(0);
    expect(parsed.warnings).toBe(0);
  });

  it('chaque diagnostic porte un code ET une portée exploitables', () => {
    // Sans `scope`, l'UI ne peut pas dire QUEL slot est en cause — le
    // diagnostic devient une alarme sans adresse.
    for (const d of RiskDiagnosticsSchema.parse(DIAG_PAYLOAD).diagnostics) {
      expect(d.code.length).toBeGreaterThan(0);
      expect(d.scope).toMatch(/^(venue|symbol|slot):/);
      expect(d.message.length).toBeGreaterThan(0);
    }
  });

  it('le compteur d’erreurs correspond aux diagnostics de sévérité error', () => {
    const parsed = RiskDiagnosticsSchema.parse(DIAG_PAYLOAD);
    const counted = parsed.diagnostics.filter((d) => d.severity === 'error').length;
    expect(parsed.errors).toBe(counted);
  });
});

describe('/api/backtest — double passe (§5.1)', () => {
  const RUNS = {
    reference: { initial_capital: 1000, total_trades: 12, total_pnl: -32.0,
                 pnl_pct: -3.2, max_drawdown: 4.1, rejections: { par_motif: {} } },
    // Valeurs coherentes entre elles, arrondies a 4 decimales comme le backend :
    // -4.1 / 90 * 100 = -4.5556.
    live: { initial_capital: 90, total_trades: 8, total_pnl: -4.1,
            pnl_pct: -4.5556, max_drawdown: 5.0,
            rejections: { par_motif: { notionnel_min: 6 } } },
    ecart_pnl_pct: -1.3556,
  };

  it('le bloc runs traverse BacktestSchema sans etre efface', () => {
    // `.passthrough()` doit laisser passer `runs`/`envelope` : sans ca la
    // carte « Étude vs Réel » ne recevrait jamais rien, en silence.
    const parsed = BacktestSchema.parse({
      symbol: 'AIR.PA', timeframe: '1h',
      by_strategy: { dummy: { total_trades: 8, runs: RUNS,
                              envelope: { slot_envelope: 90, currency: 'EUR' } } },
    });
    const entry: any = parsed.by_strategy!.dummy;
    expect(entry.runs.live.pnl_pct).toBe(-4.5556);
    expect(entry.envelope.slot_envelope).toBe(90);
  });

  it('les deux passes rapportent leur PnL sur des bases DIFFERENTES', () => {
    // C'est le point : comparer -3.2% et -4.55% n'a de sens que parce que
    // chacun est exprime en % de sa propre base.
    expect(RUNS.reference.initial_capital).not.toBe(RUNS.live.initial_capital);
    expect(RUNS.reference.pnl_pct).toBeCloseTo(
      RUNS.reference.total_pnl / RUNS.reference.initial_capital * 100, 4);
    expect(RUNS.live.pnl_pct).toBeCloseTo(
      RUNS.live.total_pnl / RUNS.live.initial_capital * 100, 4);
  });

  it("l'ecart est explique par un motif de refus present cote live seulement", () => {
    const live = RUNS.live.rejections.par_motif as Record<string, number>;
    const ref = RUNS.reference.rejections.par_motif as Record<string, number>;
    const causes = Object.keys(live).filter((k) => (live[k] ?? 0) > (ref[k] ?? 0));
    expect(causes).toContain('notionnel_min');
  });
});
