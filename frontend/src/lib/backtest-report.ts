/**
 * Rapport imprimable d'un backtest (ARCH-02).
 *
 * Parité avec l'export PDF Jinja2, sans dépendance : le navigateur imprime.
 * Construction séparée de l'ouverture de fenêtre pour que le document produit
 * soit vérifiable — c'est du HTML assemblé par concaténation, donc de
 * l'échappement à ne pas oublier.
 */
import type { StrategyPanel } from '@/lib/backtest-verdict';

const STYLE = `
        body{font-family:system-ui,sans-serif;padding:24px;color:#111}
        h1{font-size:18px} table{border-collapse:collapse;width:100%;font-size:12px}
        th,td{border:1px solid #ddd;padding:6px 8px;text-align:left}
        th{background:#f3f4f6} .muted{color:#6b7280;font-size:11px}`;

/** Les noms de stratégie viennent de la config : jamais insérés bruts. */
function esc(v: unknown): string {
  return String(v ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const n2 = (v: unknown, suffixe = '') => `${Number(v ?? 0).toFixed(2)}${suffixe}`;

export function buildBacktestReportHtml(
  strategies: Array<readonly [string, StrategyPanel]>,
  meta: { symbol?: string; timeframe?: string; date?: Date } = {},
): string {
  const rows = strategies.map(([name, s]) =>
    `<tr><td>${esc(name)}</td><td>${esc(s.total_trades ?? '—')}</td>`
    + `<td>${Number(s.win_rate ?? 0).toFixed(1)}%</td>`
    + `<td>${n2(s.total_pnl)}</td>`
    + `<td>${n2(s.sharpe)}</td>`
    + `<td>${n2(s.max_drawdown, '%')}</td></tr>`).join('');

  const quand = (meta.date ?? new Date()).toLocaleString('fr-FR');
  return `<!DOCTYPE html><html><head><title>Backtest</title>
      <style>${STYLE}
      </style></head><body>
      <h1>Rapport backtest</h1>
      <p class="muted">${esc(quand)} · ${esc(meta.symbol)} ${esc(meta.timeframe)}</p>
      <table><thead><tr><th>Stratégie</th><th>Trades</th><th>WR</th><th>PnL</th><th>Sharpe</th><th>Max DD</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="muted">Imprimer → Enregistrer au format PDF</p>
      <script>window.onload=function(){window.print()}</script>
      </body></html>`;
}
