import { describe, it, expect } from 'vitest';

import { normalizeMlInfo } from '@/lib/backend-normalizers';

/**
 * Régression : `normalizeMlInfo` ne reconnaissait pas la forme que le backend
 * produit réellement — `{mode, symbol, timeframe, models: {nom: entry}}` (cf.
 * `Backtester.run`). Elle tombait dans la branche « objet plat » et lisait
 * `mlInfo.auc`, absent à ce niveau : le panneau ML affichait donc « — » sur ses
 * quatre indicateurs à chaque backtest de stratégie ML.
 *
 * Le même oubli masquait `overlap_warning` — la détection de fuite temporelle
 * que le serveur calcule depuis toujours mais n'écrivait qu'au log.
 */

const PAYLOAD_BACKEND = {
  mode: 'frozen',
  symbol: 'BTC/USDC',
  timeframe: '1h',
  models: {
    ml_dynamic_threshold: {
      resolved: true,
      fallback_to_inline: false,
      version_id: '2024-06-01_ab12cd34',
      train_start: '2023-01-01',
      train_end: '2024-06-01',
      auc: 0.612,
      overlap_warning: true,
    },
  },
};

describe('normalizeMlInfo — forme réelle du backend', () => {
  it('lit les métriques sous `models[strategy]`', () => {
    const info = normalizeMlInfo(PAYLOAD_BACKEND, 'ml_dynamic_threshold');
    expect(info).not.toBeNull();
    expect(info!.auc).toBe(0.612);
    expect(info!.model_version).toBe('2024-06-01_ab12cd34');
  });

  it('expose la fuite temporelle et la fenêtre d’entraînement', () => {
    const info = normalizeMlInfo(PAYLOAD_BACKEND, 'ml_dynamic_threshold')!;
    expect(info.overlap_warning).toBe(true);
    expect(info.train_start).toBe('2023-01-01');
    expect(info.train_end).toBe('2024-06-01');
  });

  it('retombe sur le premier modèle si la stratégie n’est pas dans le dict', () => {
    const info = normalizeMlInfo(PAYLOAD_BACKEND, 'autre_strategie')!;
    expect(info.auc).toBe(0.612);
  });

  it('signale le repli sur entraînement inline', () => {
    const info = normalizeMlInfo(
      { models: { s: { resolved: false, fallback_to_inline: true } } }, 's')!;
    expect(info.fallback_to_inline).toBe(true);
    expect(info.auc).toBeNull();
  });
});

describe('normalizeMlInfo — formes historiques préservées', () => {
  it('accepte un tableau indexé par stratégie', () => {
    const info = normalizeMlInfo(
      [{ strategy: 'a', auc: 0.5 }, { strategy: 'b', auc: 0.7 }], 'b')!;
    expect(info.auc).toBe(0.7);
  });

  it('accepte un objet déjà aplati', () => {
    const info = normalizeMlInfo({ auc: 0.55, n_features: 462 }, 'x')!;
    expect(info.auc).toBe(0.55);
    expect(info.n_features).toBe(462);
    expect(info.overlap_warning).toBe(false);
  });

  it('renvoie null sur une entrée inexploitable', () => {
    expect(normalizeMlInfo(null, 'x')).toBeNull();
    expect(normalizeMlInfo('bruit', 'x')).toBeNull();
  });
});
