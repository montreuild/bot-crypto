/**
 * OPT-001 — Before/After grid pour les job cards de l'optimiseur.
 *
 * Extrait du template Jinja2 `optimizer.html:648-671`.
 * Compare 6 métriques avant vs après optimisation (OOS).
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

interface Metrics {
  trades: number;
  pnl: number;
  sharpe: number;
  win_rate: number;
  max_drawdown: number;
  alpha: number;
}

interface Props {
  baseline: Metrics | null;
  baselineSource?: string;
  after: Metrics | null;
}

function fmt(v: number | null | undefined, decimals = 2, prefix = '', suffix = ''): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${prefix}${v.toFixed(decimals)}${suffix}`;
}

function fmtSigned(v: number | null | undefined, decimals = 2, prefix = '', suffix = ''): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = v >= 0 ? '+' : '';
  return `${sign}${prefix}${v.toFixed(decimals)}${suffix}`;
}

function delta(before: number | null | undefined, after: number | null | undefined): { text: string; tone: 'green' | 'red' | 'muted' } {
  if (before == null || after == null) return { text: '—', tone: 'muted' };
  const d = after - before;
  const sign = d >= 0 ? '+' : '';
  const tone = d > 0.001 ? 'green' : d < -0.001 ? 'red' : 'muted';
  return { text: `${sign}${d.toFixed(2)}`, tone };
}

export function BeforeAfterGrid({ baseline, baselineSource, after }: Props) {
  if (!baseline && !after) {
    return (
      <div className="text-xs text-muted-foreground italic px-3 py-2">
        Pas de baseline disponible — lancez l&apos;optimisation pour comparer.
      </div>
    );
  }

  const rows: Array<{ label: string; before: string; after: string; delta: { text: string; tone: string } }> = [
    {
      label: 'Trades',
      before: fmt(baseline?.trades, 0),
      after: fmt(after?.trades, 0),
      delta: delta(baseline?.trades, after?.trades),
    },
    {
      label: 'PnL',
      before: fmtSigned(baseline?.pnl, 2, '$'),
      after: fmtSigned(after?.pnl, 2, '$'),
      delta: delta(baseline?.pnl, after?.pnl),
    },
    {
      label: 'Sharpe',
      before: fmt(baseline?.sharpe, 2),
      after: fmt(after?.sharpe, 2),
      delta: delta(baseline?.sharpe, after?.sharpe),
    },
    {
      label: 'Win Rate',
      before: fmt(baseline?.win_rate, 1, '', '%'),
      after: fmt(after?.win_rate, 1, '', '%'),
      delta: delta(baseline?.win_rate, after?.win_rate),
    },
    {
      label: 'Drawdown',
      before: fmt(baseline?.max_drawdown, 1, '', '%'),
      after: fmt(after?.max_drawdown, 1, '', '%'),
      delta: delta(baseline?.max_drawdown, after?.max_drawdown),
    },
    {
      label: 'Alpha',
      before: fmtSigned(baseline?.alpha, 1, '', '%'),
      after: fmtSigned(after?.alpha, 1, '', '%'),
      delta: delta(baseline?.alpha, after?.alpha),
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          📊 Avant / Après
          {baselineSource && (
            <span className="ml-2 text-xs text-muted-foreground font-normal">
              (baseline: {baselineSource})
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th className="text-left py-1">Métrique</th>
              <th className="text-right py-1 px-2">Avant</th>
              <th className="text-right py-1 px-2">Après (OOS)</th>
              <th className="text-right py-1 px-2">Δ</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-b border-border/50">
                <td className="py-1 font-medium">{r.label}</td>
                <td className="text-right py-1 px-2 font-mono text-muted-foreground">{r.before}</td>
                <td className="text-right py-1 px-2 font-mono">{r.after}</td>
                <td
                  className={`text-right py-1 px-2 font-mono ${
                    r.delta.tone === 'green' ? 'text-emerald-400' : r.delta.tone === 'red' ? 'text-rose-400' : 'text-muted-foreground'
                  }`}
                >
                  {r.delta.text}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
