'use client';

/**
 * S3-F1-US1 — Page Portefeuille unifiée (fusion Dashboard + Portfolio).
 *
 * Stratégie strangler fig : cette page coexiste avec /dashboard et /portfolio
 * existants. Une fois validée par les utilisateurs, une redirection 308 sera
 * posée depuis /dashboard et /portfolio vers /portfolio-v2, puis cette page
 * deviendra /portfolio (page d'accueil).
 *
 * Différences vs Dashboard actuel :
 *  - Bandeau de santé en français courant (HealthBanner)
 *  - Donut d'allocation (AllocationDonut)
 *  - Bouton arrêt d'urgence visible
 *  - Positions regroupées par bot
 *  - Fil d'activité mis en avant (miroir Telegram)
 *  - Jauges de risque visuelles
 *
 * Différences vs Portfolio actuel :
 *  - Equity curve réelle en haut (pas en bas)
 *  - KPIs consolidés (capital, PnL, WR, DD)
 *  - Layout plus dense et plus lisible
 */

import {
  CapitalCard, PnLCard, WinRateCard, ProfitFactorCard, DrawdownCard,
} from '@/components/cards/kpi-cards';
import { EquityCurve } from '@/components/charts/equity-curve';
import { PositionsTable } from '@/components/cards/positions-table';
import { LiveTradesFeed } from '@/components/cards/live-trades-feed';
import { SignalsFeed } from '@/components/cards/signals-feed';
import { RiskPanel } from '@/components/cards/risk-panel';
import { HealthBanner } from '@/components/cards/health-banner';
import { AllocationDonut } from '@/components/cards/allocation-donut';
import { Button } from '@/components/ui/button';
import { QueryBoundary } from '@/components/ui/query-state';
import { useBotStatus, usePortfolio } from '@/hooks/use-api';
import { Play, Square, AlertTriangle } from 'lucide-react';
import { useState } from 'react';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { useQueryClient } from '@tanstack/react-query';

export default function PortfolioV2Page() {
  const statusQuery = useBotStatus();
  const portfolioQuery = usePortfolio();
  const qc = useQueryClient();

  const { data: status } = statusQuery;
  const { data: portfolio } = portfolioQuery;

  const [stopDialogOpen, setStopDialogOpen] = useState(false);
  const [stopLoading, setStopLoading] = useState(false);
  const [startLoading, setStartLoading] = useState(false);

  const handleStart = async () => {
    setStartLoading(true);
    try {
      await api.startBot();
      toast.success('Trader démarré');
      qc.invalidateQueries({ queryKey: ['status'] });
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    } finally {
      setStartLoading(false);
    }
  };

  const handleStop = async (closePositions: boolean) => {
    setStopLoading(true);
    try {
      await api.stopBot(closePositions);
      toast.success(closePositions ? 'Trader arrêté, positions clôturées' : 'Trader arrêté');
      qc.invalidateQueries({ queryKey: ['status'] });
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    } finally {
      setStopLoading(false);
    }
  };

  const isRunning = status?.status === 'running';
  const hasCircuitBreaker = status?.circuit_breaker_active;

  const header = (
    <div className="flex items-end justify-between flex-wrap gap-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Portefeuille</h1>
        <p className="text-sm text-muted mt-1">
          {status
            ? `Vue consolidée · ${status.timeframes?.length || 0} TFs · ${status.strategies?.length || 0} stratégies`
            : 'Vue consolidée du portefeuille'}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {hasCircuitBreaker && (
          <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-amber-500/10 border border-amber-500/30 text-amber-400 text-xs font-medium">
            <AlertTriangle className="w-3.5 h-3.5" />
            Circuit Breaker Actif
          </span>
        )}
        {isRunning ? (
          <Button variant="danger" onClick={() => setStopDialogOpen(true)} disabled={stopLoading}>
            <Square className="w-4 h-4" />
            Arrêter
          </Button>
        ) : (
          <Button variant="success" onClick={handleStart} disabled={startLoading}>
            <Play className="w-4 h-4" />
            Démarrer
          </Button>
        )}
      </div>
    </div>
  );

  if (!status) {
    return (
      <QueryBoundary
        title={header}
        query={statusQuery}
        loadingLabel="Chargement du portefeuille…"
        onRetry={() => statusQuery.refetch()}
      >
        {null}
      </QueryBoundary>
    );
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {header}

      {/* Bandeau de santé en français */}
      <HealthBanner status={status} portfolio={portfolio} />

      {/* KPIs row */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        <CapitalCard value={status.capital || 1000} />
        <PnLCard value={status.total_pnl || 0} pct={status.total_pnl_pct || 0} />
        <WinRateCard value={status.win_rate || 0} totalTrades={status.total_trades || 0} />
        <ProfitFactorCard value={status.profit_factor || 0} />
        <DrawdownCard value={status.global_dd_pct || 0} limit={status.global_dd_limit || 0.20} />
      </div>

      {/* Equity + Allocation donut row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <EquityCurve />
        </div>
        <AllocationDonut
          slots={status.capital_allocation}
          capital={status.capital || 1000}
          shadowAllocation={portfolio?.shadow_allocation}
        />
      </div>

      {/* Risk + Activity feed */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <RiskPanel />
        <div className="lg:col-span-2">
          <LiveTradesFeed />
        </div>
      </div>

      {/* Positions + Signals */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <PositionsTable />
        <SignalsFeed />
      </div>

      {/* Performance par stratégie (si données) */}
      {status.by_strategy && Object.keys(status.by_strategy).length > 0 && (
        <div className="rounded-xl border border-border bg-card p-5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted mb-4">
            Performance par Stratégie
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-dim border-b border-border">
                  <th scope="col" className="pb-2 font-medium">Stratégie</th>
                  <th scope="col" className="pb-2 font-medium text-right">Trades</th>
                  <th scope="col" className="pb-2 font-medium text-right">Win Rate</th>
                  <th scope="col" className="pb-2 font-medium text-right">PnL</th>
                  <th scope="col" className="pb-2 font-medium text-right">PF</th>
                  <th scope="col" className="pb-2 font-medium text-right">Sharpe</th>
                  <th scope="col" className="pb-2 font-medium text-right">Max DD</th>
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

      {/* Modal stop bot avec choix close_positions */}
      <ConfirmDialog
        open={stopDialogOpen}
        onOpenChange={setStopDialogOpen}
        title="Arrêter le trader ?"
        description="Le bot cessera d'émettre de nouveaux signaux. Les positions ouvertes peuvent être conservées (suivi manuel) ou clôturées immédiatement."
        confirmLabel="Conserver les positions"
        cancelLabel="Annuler"
        variant="danger"
        isLoading={stopLoading}
        onConfirm={() => handleStop(false)}
      />
    </div>
  );
}
