'use client';

import {
  CapitalCard, PnLCard, WinRateCard, ProfitFactorCard, DrawdownCard,
} from '@/components/cards/kpi-cards';
import { EquityCurve } from '@/components/charts/equity-curve';
import { PositionsTable } from '@/components/cards/positions-table';
import { LiveTradesFeed } from '@/components/cards/live-trades-feed';
import { SignalsFeed } from '@/components/cards/signals-feed';
import { AllocationsGrid } from '@/components/cards/allocations-grid';
import { RiskPanel } from '@/components/cards/risk-panel';
import { useBotStatus } from '@/hooks/use-api';
import { QueryBoundary } from '@/components/ui/query-state';

export default function DashboardPage() {
  const { data: status, isLoading, error, refetch, isRefetching } = useBotStatus();

  // S6-12 : le titre est monté avant la garde — la page garde son `h1` même
  // backend éteint, et l'échec s'affiche au lieu de tourner indéfiniment.
  const header = (
    <div className="flex items-end justify-between">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted mt-1">
          {status
            ? `Vue temps réel du trading · ${status.timeframes?.length || 0} TFs · ${status.strategies?.length || 0} stratégies actives`
            : 'Vue temps réel du trading'}
        </p>
      </div>
      {status && (
        <div className="flex items-center gap-2 text-xs text-dim font-mono">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          Live · mis à jour il y a {status.last_scan_time ? 'quelques secondes' : '—'}
        </div>
      )}
    </div>
  );

  if (error || isLoading || !status) {
    return (
      <QueryBoundary
        title={header}
        error={error}
        isLoading={isLoading || !status}
        loadingLabel="Chargement du dashboard…"
        onRetry={() => refetch()}
        isRetrying={isRefetching}
      >
        {null}
      </QueryBoundary>
    );
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {header}

      {/* KPIs row */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        <CapitalCard value={status.capital || 1000} />
        <PnLCard value={status.total_pnl || 0} pct={status.total_pnl_pct || 0} />
        <WinRateCard value={status.win_rate || 0} totalTrades={status.total_trades || 0} />
        <ProfitFactorCard value={status.profit_factor || 0} />
        <DrawdownCard value={status.global_dd_pct || 0} limit={status.global_dd_limit || 0.20} />
      </div>

      {/* Equity + Risk row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <EquityCurve />
        </div>
        <RiskPanel />
      </div>

      {/* Positions + Live trades */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <PositionsTable />
        <LiveTradesFeed />
      </div>

      {/* Signals + Allocations */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SignalsFeed />
        <AllocationsGrid />
      </div>

      {/* By strategy stats */}
      {status.by_strategy && Object.keys(status.by_strategy).length > 0 && (
        <div className="rounded-xl border border-border bg-card p-5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted mb-4">
            Performance par Stratégie
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-dim border-b border-border">
                  <th className="pb-2 font-medium">Stratégie</th>
                  <th className="pb-2 font-medium text-right">Trades</th>
                  <th className="pb-2 font-medium text-right">Win Rate</th>
                  <th className="pb-2 font-medium text-right">PnL</th>
                  <th className="pb-2 font-medium text-right">PF</th>
                  <th className="pb-2 font-medium text-right">Sharpe</th>
                  <th className="pb-2 font-medium text-right">Max DD</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(status.by_strategy).map(([name, stats]: [string, any]) => (
                  <tr key={name} className="border-b border-border/50 hover:bg-card-hover">
                    <td className="py-2.5 font-medium">{name}</td>
                    <td className="py-2.5 text-right font-mono text-muted">{stats.total_trades}</td>
                    <td className={`py-2.5 text-right font-mono ${stats.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {stats.win_rate.toFixed(1)}%
                    </td>
                    <td className={`py-2.5 text-right font-mono ${stats.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {stats.total_pnl >= 0 ? '+' : ''}{stats.total_pnl.toFixed(2)}
                    </td>
                    <td className="py-2.5 text-right font-mono text-muted">
                      {stats.profit_factor === 999 ? '∞' : stats.profit_factor.toFixed(2)}
                    </td>
                    <td className="py-2.5 text-right font-mono text-muted">{stats.sharpe.toFixed(2)}</td>
                    <td className="py-2.5 text-right font-mono text-red-400">{stats.max_drawdown.toFixed(2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
