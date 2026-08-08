/**
 * BT-007 — Tableau comparatif multi-stratégies (best value ✦).
 *
 * Extrait du template Jinja2 `backtest.html:757-768` (`buildCmp`).
 *
 * F1 (refactor) : la table est désormais rendue par le composant générique
 * `<DataTable>`. Stratégies en lignes, métriques en colonnes — pattern naturel
 * pour DataTable et cohérent avec `compare-view.tsx` qui a la même structure.
 *
 * 11 colonnes : Strategy, Trades, Win Rate, PnL net, Max DD, Sharpe, Sortino,
 * Calmar, Expectancy, Profit Factor, Alpha. Les 3 nouvelles colonnes (Sortino,
 * Calmar, alpha_vs_bh) viennent du QW-1 (métriques étendues S3-07).
 *
 * La meilleure valeur de chaque colonne est surlignée (✦).
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import type { BacktestResult } from '@/types';

interface Props {
  strategies: BacktestResult[];
}

function fmt(v: number | null | undefined, decimals = 2, prefix = '', suffix = ''): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${prefix}${v.toFixed(decimals)}${suffix}`;
}

function isBest(
  value: number | null | undefined,
  all: Array<number | null | undefined>,
  higherIsBetter = true,
): boolean {
  if (value == null) return false;
  const valid = all.filter((v) => v != null && Number.isFinite(v as number));
  if (valid.length === 0) return false;
  const target = higherIsBetter ? Math.max(...(valid as number[])) : Math.min(...(valid as number[]));
  return Math.abs((value as number) - target) < 1e-9;
}

export function StrategyComparisonTable({ strategies }: Props) {
  if (strategies.length < 2) return null;

  // Pour chaque métrique, on calcule la liste des valeurs pour détecter le best.
  const allTrades = strategies.map((s) => s.total_trades);
  const allWr = strategies.map((s) => s.win_rate);
  const allPnl = strategies.map((s) => s.total_pnl);
  const allDd = strategies.map((s) => s.max_drawdown);
  const allSharpe = strategies.map((s) => s.sharpe);
  const allSortino = strategies.map((s) => s.sortino ?? 0);
  const allCalmar = strategies.map((s) => s.calmar ?? 0);
  const allExp = strategies.map((s) => s.expectancy);
  const allPf = strategies.map((s) => s.profit_factor);
  const allAlpha = strategies.map((s) => s.alpha ?? 0);

  // Helper pour rendre une cellule avec surlignage best.
  const renderCell = (
    value: number | null | undefined,
    all: Array<number | null | undefined>,
    format: (v: number) => string,
    higherIsBetter = true,
  ) => {
    const best = isBest(value, all, higherIsBetter);
    return (
      <span className={`font-mono ${best ? 'text-emerald-400 font-bold' : ''}`}>
        {value == null ? '—' : format(value as number)}
        {best && <span className="ml-1">✦</span>}
      </span>
    );
  };

  const columns: DataTableColumn<BacktestResult>[] = [
    {
      key: 'strategy',
      header: 'Stratégie',
      align: 'left',
      render: (s) => <span className="font-medium text-cyan-400">{s.strategy}</span>,
    },
    {
      key: 'total_trades',
      header: 'Trades',
      align: 'right',
      render: (s) => renderCell(s.total_trades, allTrades, (v) => String(v)),
    },
    {
      key: 'win_rate',
      header: 'Win Rate',
      align: 'right',
      render: (s) => renderCell(s.win_rate, allWr, (v) => `${(v * 100).toFixed(1)}%`),
    },
    {
      key: 'total_pnl',
      header: 'PnL net',
      align: 'right',
      sortValue: (s) => s.total_pnl,
      render: (s) =>
        renderCell(s.total_pnl, allPnl, (v) => fmt(v, 2, v >= 0 ? '+' : '', '$')),
    },
    {
      key: 'max_drawdown',
      header: 'Max DD',
      align: 'right',
      render: (s) => renderCell(s.max_drawdown, allDd, (v) => `-${(v * 100).toFixed(1)}%`, false),
    },
    {
      key: 'sharpe',
      header: 'Sharpe',
      align: 'right',
      render: (s) => renderCell(s.sharpe, allSharpe, (v) => v.toFixed(2)),
    },
    {
      key: 'sortino',
      header: 'Sortino',
      align: 'right',
      render: (s) => renderCell(s.sortino ?? 0, allSortino, (v) => v.toFixed(2)),
    },
    {
      key: 'calmar',
      header: 'Calmar',
      align: 'right',
      render: (s) => renderCell(s.calmar ?? 0, allCalmar, (v) => v.toFixed(2)),
    },
    {
      key: 'expectancy',
      header: 'Expectancy',
      align: 'right',
      render: (s) =>
        renderCell(s.expectancy, allExp, (v) => fmt(v, 2, v >= 0 ? '+' : '', '$')),
    },
    {
      key: 'profit_factor',
      header: 'Profit Factor',
      align: 'right',
      render: (s) => renderCell(s.profit_factor, allPf, (v) => v.toFixed(2)),
    },
    {
      key: 'alpha',
      header: 'Alpha',
      align: 'right',
      render: (s) => renderCell(s.alpha ?? 0, allAlpha, (v) => fmt(v, 1, v >= 0 ? '+' : '', '%')),
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          📊 Comparatif — {strategies.length} stratégies
        </CardTitle>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={columns}
          rows={strategies}
          sortable
          initialSortKey="total_pnl"
          initialSortAsc={false}
          rowKey={(s) => `strat-${s.strategy}`}
          emptyLabel="Aucune stratégie à comparer"
        />
      </CardContent>
    </Card>
  );
}
