/**
 * ARCH-02 — le rapport imprimable, vérifiable au lieu d'être écrit à l'aveugle.
 *
 * Le HTML était assemblé par concaténation directement dans le `onClick`, et
 * les noms de stratégie y étaient insérés bruts.
 */
import { describe, expect, it } from 'vitest';

import { buildBacktestReportHtml } from '@/lib/backtest-report';
import type { StrategyPanel } from '@/lib/backtest-verdict';

const s = (o: Partial<StrategyPanel>) => o as StrategyPanel;
const LE_2026 = new Date(2026, 7, 22, 14, 30);

describe('buildBacktestReportHtml', () => {
  it('rend une ligne par stratégie, avec les six colonnes', () => {
    const html = buildBacktestReportHtml([
      ['smart_money', s({ total_trades: 9, win_rate: 44.4, total_pnl: -14.71,
                          sharpe: 0.12, max_drawdown: -3.2 })],
    ], { symbol: 'BTC/USDC', timeframe: '1h', date: LE_2026 });

    expect(html).toContain('<td>smart_money</td>');
    expect(html).toContain('<td>9</td>');
    expect(html).toContain('<td>44.4%</td>');
    expect(html).toContain('<td>-14.71</td>');
    expect(html).toContain('<td>0.12</td>');
    expect(html).toContain('<td>-3.20%</td>');
    expect(html).toContain('BTC/USDC');
    expect(html).toContain('1h');
  });

  it('un compte de trades absent s’affiche « — », pas « 0 »', () => {
    const html = buildBacktestReportHtml([['x', s({})]]);
    expect(html).toContain('<td>—</td>');
  });

  it('les métriques absentes valent zéro, pas NaN', () => {
    const html = buildBacktestReportHtml([['x', s({})]]);
    expect(html).not.toContain('NaN');
    expect(html).toContain('<td>0.00</td>');
  });

  it('échappe le nom de stratégie', () => {
    const html = buildBacktestReportHtml([
      ['<script>alert(1)</script>', s({ total_trades: 1 })],
    ]);
    expect(html).not.toContain('<script>alert(1)</script>');
    expect(html).toContain('&lt;script&gt;');
  });

  it('échappe aussi le symbole et le timeframe', () => {
    const html = buildBacktestReportHtml([], { symbol: '"><b>x</b>', timeframe: '1h' });
    expect(html).not.toContain('<b>x</b>');
    expect(html).toContain('&lt;b&gt;');
  });

  it('sans stratégie, le document reste valide et le tableau vide', () => {
    const html = buildBacktestReportHtml([]);
    expect(html).toContain('<tbody></tbody>');
    expect(html).toContain('Rapport backtest');
  });

  it('déclenche l’impression au chargement', () => {
    expect(buildBacktestReportHtml([])).toContain('window.print()');
  });
});
