'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { CsvExportButton, JsonExportButton } from '@/components/ui/export-buttons';
import { WalkForwardTable } from '@/components/charts/walk-forward-table';
import { StudyVsLiveCard } from '@/components/cards/study-vs-live-card';
import { MonteCarloPanel } from '@/components/charts/monte-carlo-panel';
import { TradesScatter } from '@/components/charts/trades-scatter';
import { BacktestEquityChart } from '@/components/charts/backtest-equity-chart';
import { CostBreakdownCard } from '@/components/cards/cost-breakdown-card';
import { toast } from 'sonner';
import {
  AlertCircle, CheckCircle2, TrendingUp, Rocket,
  Maximize2, FileDown, X, Shield, ChevronDown, ChevronUp,
} from 'lucide-react';
import { cn, formatMoney, quoteCurrency } from '@/lib/utils';
import { LoadingState, ErrorState } from '@/components/ui/query-state';
import { PriceSignalsChart } from '@/components/charts/price-signals-chart';
import { TradesTable } from '@/components/tables/trades-table';
import { DiagnosticsPanel } from '@/components/cards/diagnostics-panel';
import { RecommendationsPanel } from '@/components/cards/recommendations-panel';
import { StrategyComparisonTable } from '@/components/cards/strategy-comparison-table';
import { TradesStatsPanel } from '@/components/cards/trades-stats-panel';
import { MLBacktestPanel } from '@/components/cards/ml-backtest-panel';
import { CostSimulatorPanel } from '@/components/cards/cost-simulator-panel';
import { recommendedThreshold } from '@/lib/strat-thresholds';
import { normalizeDiagnostics, equityFinal, buyHold, equityValues, alphaPct, formatDrawdownPct } from '@/lib/backend-normalizers';
import type { BacktestResult, StrategyStats, BacktestTrade } from '@/types';

type StrategyPanel = StrategyStats & {
  trades?: number | BacktestTrade[];
  walk_forward?: BacktestResult['walk_forward'];
  runs?: BacktestResult['runs'];
  envelope?: unknown;
  realistic_risk_diagnostics?: BacktestResult['realistic_risk_diagnostics'];
  diagnostics?: BacktestResult['diagnostics'];
  ml_info?: BacktestResult['ml_info'];
  equity_final?: number;
  final_equity?: number;
  buy_and_hold_pct?: number;
};

function unwrapBacktest(result: BacktestResult | BacktestResult[]): BacktestResult {
  return Array.isArray(result) ? result[0] : result;
}

function strategyMap(r: BacktestResult): Record<string, StrategyPanel> {
  return (r?.by_strategy || {}) as Record<string, StrategyPanel>;
}

export function Verdict({ result }: { result: BacktestResult | BacktestResult[] }) {
  const r = unwrapBacktest(result);
  const ccy = quoteCurrency(r);
  const byStrategy = strategyMap(r);
  const strategyNames = Object.keys(byStrategy);

  if (strategyNames.length === 0) {
    return (
      <Card className="border-amber-500/30 bg-amber-500/5">
        <CardContent className="p-4 flex items-center gap-3">
          <AlertCircle className="w-5 h-5 text-amber-400" />
          <p className="text-sm">
            <span className="font-medium">Pas de trades générés. </span>
            <span className="text-muted">Ajuste la période ou les stratégies sélectionnées.</span>
          </p>
        </CardContent>
      </Card>
    );
  }

  // Trouve la meilleure stratégie
  const bestEntry = strategyNames
    .map((name) => [name, byStrategy[name]] as const)
    .sort(([, a], [, b]) => (b.total_pnl ?? 0) - (a.total_pnl ?? 0))[0];
  const [bestName, bestStats] = bestEntry;
  const pnl = bestStats.total_pnl ?? 0;
  const wr = bestStats.win_rate ?? 0;
  const sharpe = bestStats.sharpe ?? 0;
  const maxDd = bestStats.max_drawdown ?? 0;
  const trades = bestStats.total_trades ?? 0;

  // Verdict logic
  const isPositive = pnl > 0;
  const isStrong = sharpe > 1.5 && trades >= 20 && wr >= 50;
  const isRisky = maxDd < -20 || trades < 10;

  let tone: 'positive' | 'neutral' | 'negative' = 'neutral';
  let icon: React.ReactNode = <AlertCircle className="w-5 h-5 text-amber-400" />;
  let message = '';

  if (isStrong && !isRisky) {
    tone = 'positive';
    icon = <CheckCircle2 className="w-5 h-5 text-emerald-400" />;
    message = `Edge significatif sur ${bestName} avec ${trades} trades, Sharpe ${sharpe.toFixed(2)}, max DD ${maxDd.toFixed(1)}%. Recommandation : essai avec budget 5%.`;
  } else if (isPositive && !isRisky) {
    tone = 'neutral';
    icon = <TrendingUp className="w-5 h-5 text-cyan-400" />;
    message = `Résultats positifs sur ${bestName} (${formatMoney(pnl, ccy, { sign: true })}, WR ${wr.toFixed(0)}%) mais edge limité. À valider par forward-test.`;
  } else if (isRisky) {
    tone = 'negative';
    icon = <AlertCircle className="w-5 h-5 text-amber-400" />;
    message = `Résultats risqués sur ${bestName} : ${trades} trades (${trades < 10 ? 'trop peu' : 'ok'}), max DD ${maxDd.toFixed(1)}%. À éviter en l'état.`;
  } else {
    tone = 'negative';
    icon = <AlertCircle className="w-5 h-5 text-red-400" />;
    message = `${bestName} sous-performe (${formatMoney(pnl, ccy, { sign: true })}, WR ${wr.toFixed(0)}%). Stratégie à revoir.`;
  }

  const toneClasses = {
    positive: 'border-emerald-500/30 bg-emerald-500/5',
    neutral: 'border-cyan-500/30 bg-cyan-500/5',
    negative: 'border-red-500/30 bg-red-500/5',
  };

  return (
    <Card className={toneClasses[tone]}>
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 mt-0.5">{icon}</div>
          <div className="flex-1">
            <div className="text-sm font-medium mb-1">Verdict</div>
            <p className="text-sm text-foreground">{message}</p>
            {tone === 'positive' && (
              <Button
                size="sm"
                variant="success"
                className="mt-3"
                onClick={() => toast.info('Création du bot en essai — à implémenter')}
              >
                <Rocket className="w-3.5 h-3.5" />
                Créer le bot (Essai)
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Backtest Results ─────────────────────────────────────────────────────

export function BacktestResults({
  result,
  scoreThreshold,
  loading = false,
  error,
}: {
  result?: BacktestResult | BacktestResult[] | null;
  scoreThreshold?: number | null;
  loading?: boolean;
  error?: string | null;
}) {
  const [fsStrategy, setFsStrategy] = useState<string | null>(null);
  const [collapsedTrades, setCollapsedTrades] = useState<Record<string, boolean>>({});
  if (loading) {
    return <LoadingState label="Backtest en cours…" className="min-h-[300px]" />;
  }
  if (error) {
    return <ErrorState error={error} />;
  }
  if (!result) {
    return null;
  }
  const r = unwrapBacktest(result);
  const ccy = quoteCurrency(r);
  const byStrategy = strategyMap(r);
  const strategies = Object.entries(byStrategy);

  const csvRows = strategies.map(([name, stats]) => ({ strategy: name, ...stats }));

  const comparisonStrategies: Array<Partial<BacktestResult> & { strategy: string }> =
    strategies.length > 1
      ? strategies.map(([name, stats]) => {
          const {
            trades: _count,
            monte_carlo: _mc,
            recommendations_summary: _rs,
            ...kpis
          } = stats ?? {};
          return {
            ...kpis,
            strategy: name,
            symbol: r?.symbol,
            timeframe: r?.timeframe,
          };
        })
      : [];

  const exportPdf = () => {
    // Impression / PDF navigateur (parité Jinja2 export PDF sans dépendance jspdf)
    const w = window.open('', '_blank', 'noopener,noreferrer,width=900,height=700');
    if (!w) {
      toast.error('Pop-up bloquée — autorisez les fenêtres pour l’export PDF');
      return;
    }
    const rows = strategies.map(([name, stats]) =>
      `<tr><td>${name}</td><td>${stats.total_trades ?? '—'}</td>`
      + `<td>${Number(stats.win_rate ?? 0).toFixed(1)}%</td>`
      + `<td>${Number(stats.total_pnl ?? 0).toFixed(2)}</td>`
      + `<td>${Number(stats.sharpe ?? 0).toFixed(2)}</td>`
      + `<td>${Number(stats.max_drawdown ?? 0).toFixed(2)}%</td></tr>`,
    ).join('');
    w.document.write(`<!DOCTYPE html><html><head><title>Backtest</title>
      <style>
        body{font-family:system-ui,sans-serif;padding:24px;color:#111}
        h1{font-size:18px} table{border-collapse:collapse;width:100%;font-size:12px}
        th,td{border:1px solid #ddd;padding:6px 8px;text-align:left}
        th{background:#f3f4f6} .muted{color:#6b7280;font-size:11px}
      </style></head><body>
      <h1>Rapport backtest</h1>
      <p class="muted">${new Date().toLocaleString('fr-FR')} · ${r?.symbol || ''} ${r?.timeframe || ''}</p>
      <table><thead><tr><th>Stratégie</th><th>Trades</th><th>WR</th><th>PnL</th><th>Sharpe</th><th>Max DD</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="muted">Imprimer → Enregistrer au format PDF</p>
      <script>window.onload=function(){window.print()}</script>
      </body></html>`);
    w.document.close();
  };

  return (
    <div className="space-y-4">
      {r?.ohlcv && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Bougies OHLCV ({r.symbol} · {r.timeframe})</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted">
              {r.n_bars} bougies · {r.date_from} → {r.date_to}
              {(r.requested_start_date || r.requested_end_date) && (
                <span className="text-dim ml-2">
                  (demandé : {r.requested_start_date || '…'} → {r.requested_end_date || '…'})
                </span>
              )}
              {r.refreshed && (
                <Badge variant="success" className="text-[0.55rem] px-1 py-0 ml-2 align-middle">
                  données rafraîchies
                </Badge>
              )}
              {r.gaps_warning && (
                <span className="text-amber-400 ml-2">⚠ {r.gaps_warning}</span>
              )}
            </p>
          </CardContent>
        </Card>
      )}

      <Verdict result={result} />

      <div className="flex items-center justify-end gap-2 flex-wrap">
        <CsvExportButton
          filename="backtest"
          rows={csvRows}
          headers={{
            strategy: 'Stratégie',
            total_trades: 'Trades',
            win_rate: 'Win rate',
            total_pnl: 'PnL',
            sharpe: 'Sharpe',
            max_drawdown: 'Max DD',
          }}
        />
        <JsonExportButton filename="backtest" data={result} />
        <Button size="sm" variant="outline" onClick={exportPdf}>
          <FileDown className="w-3.5 h-3.5" />
          PDF
        </Button>
      </div>

      {comparisonStrategies.length >= 2 && (
        <StrategyComparisonTable strategies={comparisonStrategies} />
      )}

      {r?.realistic_risk && (
        <Card className="border-l-4 border-l-blue-500">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <Shield className="w-4 h-4 text-blue-400" />
              Mode realistic_risk actif
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <p className="text-muted-foreground">
              Les circuit breakers (pertes consécutives, DD journalier par slot,
              trades/jour, DD journalier global, DD depuis le pic, volatility
              brake) ont été appliqués pendant le backtest.
            </p>
            {/* Chaque stratégie a son propre `Backtester`, donc son propre
                gate : les diagnostics sont PAR STRATÉGIE. Les lire à la racine
                du résultat ne renvoyait jamais rien. */}
            {Object.entries(byStrategy).map(([stratName, stats]) => {
              const diag = stats?.realistic_risk_diagnostics;
              if (!diag) return null;
              const slots = diag.slots ?? {};
              const pausedSlots = Object.entries(slots).filter(([, s]) => s.paused);
              return (
                <div key={`rr-${stratName}`} className="pt-2 border-t border-border first:border-t-0 first:pt-0">
                  <div className="text-[10px] uppercase tracking-wide text-dim font-mono mb-1">
                    {stratName}
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                    <div className="p-2 rounded-md bg-card-hover/30">
                      <div className="text-[10px] text-dim uppercase">HALT</div>
                      <div className={cn('font-mono font-semibold', diag.halted ? 'text-red-400' : 'text-emerald-400')}>
                        {diag.halted ? (diag.halt_kind === 'daily_dd' ? 'JOUR' : 'DÉFINITIF') : 'Non'}
                      </div>
                    </div>
                    <div className="p-2 rounded-md bg-card-hover/30">
                      <div className="text-[10px] text-dim uppercase">Slots pausés</div>
                      <div className="font-mono font-semibold text-amber-400">
                        {diag.n_slots_paused ?? 0}
                      </div>
                    </div>
                    <div className="p-2 rounded-md bg-card-hover/30">
                      <div className="text-[10px] text-dim uppercase">Vol brake</div>
                      <div className={cn('font-mono font-semibold', (diag.volatility_brake_bars ?? 0) > 0 ? 'text-amber-400' : 'text-emerald-400')}>
                        {/* Le nombre de bougies freinées, pas l'état à la
                            dernière bougie : c'est ce qui dit si le frein a
                            pesé sur le run. */}
                        {diag.volatility_brake_bars ?? 0} bougies
                      </div>
                    </div>
                    <div className="p-2 rounded-md bg-card-hover/30">
                      <div className="text-[10px] text-dim uppercase">Slots monitorés</div>
                      <div className="font-mono font-semibold">{Object.keys(slots).length}</div>
                    </div>
                  </div>
                  {diag.halted && (
                    <div className="p-2 rounded-md bg-red-500/10 border border-red-500/30 mt-2">
                      <span className="text-red-400 font-semibold text-xs">⚠ HALT :</span>{' '}
                      <span className="text-red-300 text-xs">{diag.halt_reason}</span>
                    </div>
                  )}
                  {pausedSlots.map(([slotKey, s]) => (
                    <div key={slotKey} className="p-2 rounded-md bg-amber-500/10 border border-amber-500/30 mt-2">
                      <span className="text-amber-400 font-semibold text-xs">⏸ Slot pausé :</span>{' '}
                      <span className="text-amber-300 text-xs font-mono">{slotKey}</span>{' '}
                      <span className="text-amber-300/80 text-xs">— {s.pause_reason}</span>
                    </div>
                  ))}
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {/* BT-011 — avertissements globaux : seuil recommandé et taille
          d'échantillon. Affichés au-dessus des KPIs pour être vus avant tout. */}
      {strategies.length > 0 && (() => {
        // `strategy` porte le nom quand le warning est actionnable dans Config
        // (seuil ajustable) ; null quand il ne l'est pas (taille d'échantillon,
        // qui se corrige en allongeant la période, pas dans les réglages).
        const warnings: Array<{ tone: 'amber' | 'red'; text: string; strategy: string | null }> = [];
        for (const [name, stats] of strategies) {
          const rec = recommendedThreshold(name);
          const cfgThreshold = typeof scoreThreshold === 'number' ? scoreThreshold : r?.score_threshold;
          if (rec != null && cfgThreshold != null && cfgThreshold < rec - 0.01) {
            warnings.push({
              tone: 'amber',
              text: `⚠ ${name} : score_threshold actuel (${cfgThreshold.toFixed(2)}) est inférieur au seuil recommandé (${rec.toFixed(2)}). Risque de sur-trade.`,
              strategy: name,
            });
          }
          const n = stats?.total_trades ?? 0;
          if (n > 0 && n < 30) {
            warnings.push({
              tone: 'amber',
              text: `⚠ ${name} : seulement ${n} trades — échantillon insuffisant (< 30) pour valider l'edge. Sharpe et win rate ne sont pas significatifs.`,
              strategy: null,
            });
          }
        }
        if (warnings.length === 0) return null;
        return (
          <div className="space-y-2">
            {warnings.map((w, i) => (
              <div
                key={`${w.tone}-${w.text.slice(0, 48)}-${i}`}
                className={
                  w.tone === 'red'
                    ? 'rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-300'
                    : 'rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300'
                }
              >
                <span>{w.text}</span>
                {/* BT-011 — rendre le warning actionnable.
                    La spec demandait un lien vers `/settings?tab=strategies&
                    strategy=<nom>` : cet onglet N'EXISTE PAS. Le front n'a
                    aucun éditeur de `strategy_params` — `api.updateStrategyParams`
                    est défini dans `lib/api.ts` mais aucun composant ne l'appelle,
                    et l'éditeur Jinja2 (`config.html`) n'a jamais été reporté.
                    Pointer vers cette route donnerait un lien mort. On envoie donc
                    vers l'optimiseur, seul chemin en place pour recalculer puis
                    appliquer les paramètres d'une stratégie. */}
                {w.strategy && (
                  <Link
                    href={`/lab?tab=optimizer&strategy=${encodeURIComponent(w.strategy)}${r?.symbol ? `&symbol=${encodeURIComponent(r.symbol)}` : ''}${r?.timeframe ? `&tf=${encodeURIComponent(r.timeframe)}` : ''}`}
                    className="ml-2 underline underline-offset-2 font-medium hover:no-underline whitespace-nowrap"
                    title={`Optimiser ${w.strategy}${r?.symbol ? ` sur ${r.symbol}` : ''}${r?.timeframe ? ` en ${r.timeframe}` : ''} puis appliquer les paramètres`}
                  >
                    Optimiser cette stratégie →
                  </Link>
                )}
              </div>
            ))}
          </div>
        );
      })()}

      {/* Fullscreen chart modal (complément ChartFullscreen déjà dans le composant) */}
      {fsStrategy && byStrategy[fsStrategy] && (
        <div className="fixed inset-0 z-50 bg-black/80 flex flex-col p-4" role="dialog" aria-modal="true" aria-label="Chart plein écran">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-mono font-semibold text-white">{fsStrategy} — plein écran</h3>
            <Button size="sm" variant="ghost" onClick={() => setFsStrategy(null)} aria-label="Fermer">
              <X className="w-4 h-4" /> Fermer
            </Button>
          </div>
          <div className="flex-1 min-h-0 bg-card rounded-lg border border-border p-2 overflow-auto">
            <BacktestEquityChart
              strategy={fsStrategy}
              equityCurve={equityValues(byStrategy[fsStrategy]?.equity_curve)}
              initialCapital={byStrategy[fsStrategy]?.initial_capital}
              buyAndHoldPnl={byStrategy[fsStrategy]?.buy_and_hold_pnl}
              alpha={byStrategy[fsStrategy]?.alpha}
            />
          </div>
        </div>
      )}

      {/* BT-006 — KPIs par stratégie. 9 métriques au lieu de 5 : PnL Net (+%),
          Win Rate (+n trades), Sharpe (⚠ si < 30 trades), Max DD, Expectancy,
          Profit Factor, Equity Finale, Buy & Hold (+%), Alpha. */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {strategies.map(([name, stats]) => {
          const nTrades = stats?.total_trades ?? 0;
          const smallSample = nTrades < 30;
          const eqFinal = equityFinal(stats ?? {});
          const bh = buyHold(stats ?? {});
          const alphaDisplay = alphaPct(stats?.alpha, stats?.initial_capital, stats?.alpha_vs_bh);
          const bhPct = bh.pct;
          const pnlPct = stats?.total_pnl && stats?.initial_capital
            ? (stats.total_pnl / stats.initial_capital) * 100
            : null;
          return (
            <Card key={name}>
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-3 gap-2">
                  <span className="font-mono text-sm font-semibold truncate">
                    {name}
                    {r?.timeframe && <span className="text-dim font-normal"> · {r.timeframe}</span>}
                  </span>
                  <Badge variant={stats.total_pnl >= 0 ? 'success' : 'danger'}>
                    {stats.total_pnl >= 0 ? '+' : ''}{formatMoney(stats.total_pnl, ccy)}
                    {pnlPct != null && (
                      <span className="ml-1 opacity-70">
                        ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)
                      </span>
                    )}
                  </Badge>
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div>
                    <div className="text-dim">Trades</div>
                    <div className="font-mono font-semibold">
                      {nTrades}
                      {stats?.win_rate != null && (
                        <span className="text-dim ml-1">({Math.round((stats.win_rate / 100) * nTrades)}w)</span>
                      )}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Win Rate</div>
                    <div className={cn('font-mono font-semibold', stats.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400')}>
                      {stats.win_rate?.toFixed(1) ?? '—'}%
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Sharpe{smallSample ? ' ⚠' : ''}</div>
                    <div
                      className={cn(
                        'font-mono font-semibold',
                        smallSample ? 'text-amber-400' : '',
                      )}
                      title={smallSample ? '< 30 trades — Sharpe non significatif' : undefined}
                    >
                      {stats.sharpe?.toFixed(2) ?? '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Max DD</div>
                    <div className="font-mono font-semibold text-red-400">
                      {formatDrawdownPct(stats.max_drawdown)}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Expectancy</div>
                    <div className={cn('font-mono font-semibold', (stats.expectancy ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                      {stats.expectancy != null
                        ? `${stats.expectancy >= 0 ? '+' : ''}${formatMoney(stats.expectancy, ccy)}`
                        : '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">PF</div>
                    <div className="font-mono font-semibold">
                      {stats.profit_factor === 999 ? '∞' : stats.profit_factor?.toFixed(2) ?? '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Equity Finale</div>
                    <div className="font-mono font-semibold">
                      {eqFinal != null ? formatMoney(eqFinal, ccy) : '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-dim">Buy &amp; Hold</div>
                    <div className={cn('font-mono font-semibold', (bh.pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                      {bh.pnl != null
                        ? `${bh.pnl >= 0 ? '+' : ''}${formatMoney(bh.pnl, ccy)}`
                        : '—'}
                      {bhPct != null && (
                        <span className="text-dim ml-1">
                          ({bhPct >= 0 ? '+' : ''}{bhPct.toFixed(1)}%)
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="min-w-0">
                    <div className="text-dim">Alpha</div>
                    <div className={cn(
                      'font-mono font-semibold whitespace-nowrap',
                      (alphaDisplay ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400',
                    )}>
                      {alphaDisplay != null
                        ? `${alphaDisplay >= 0 ? '+' : ''}${alphaDisplay.toFixed(1)}%`
                        : '—'}
                    </div>
                  </div>
                </div>
                {stats?.recommendations && stats.recommendations.length > 0 && (
                  <div className="mt-3">
                    <RecommendationsPanel
                      strategy={name}
                      recommendations={stats.recommendations}
                      summary={stats.recommendations_summary ?? undefined}
                      defaultCollapsed
                      className="border-0 shadow-none p-0 bg-transparent"
                    />
                  </div>
                )}

              </CardContent>
            </Card>
          );
        })}
      </div>

      {strategies.map(([name, stats], idx) => {
        const trades = Array.isArray(stats?.trades) ? stats.trades : [];
        const hasDetail = stats?.equity_curve?.length || stats?.walk_forward
          || stats?.monte_carlo || stats?.runs || trades.length > 0;
        if (!hasDetail) return null;
        const candles = r?.ohlcv;
        const isMl = String(name).startsWith('ml_');
        const tradesClosed = collapsedTrades[name] ?? false;
        return (
          <div key={`detail-${name}`} className="space-y-4">
            <div className="flex items-center justify-between pt-2">
              <h3 className="text-xs uppercase tracking-wide text-dim">
                {name}{r?.timeframe ? ` · ${r.timeframe}` : ''}
              </h3>
              <Button size="sm" variant="ghost" onClick={() => setFsStrategy(name)} title="Plein écran">
                <Maximize2 className="w-3.5 h-3.5" />
                Plein écran
              </Button>
            </div>

            {stats.runs && (
              <StudyVsLiveCard
                runs={stats.runs as React.ComponentProps<typeof StudyVsLiveCard>['runs']}
                envelope={stats.envelope as React.ComponentProps<typeof StudyVsLiveCard>['envelope']}
              />
            )}

            {process.env.NEXT_PUBLIC_LAB_PRICE_CHART !== 'false' && candles && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Prix &amp; signaux — {name}</CardTitle>
                </CardHeader>
                <CardContent>
                  <PriceSignalsChart candles={candles} trades={trades} />
                </CardContent>
              </Card>
            )}

            <BacktestEquityChart
              strategy={name}
              equityCurve={equityValues(stats.equity_curve)}
              initialCapital={stats.initial_capital}
              buyAndHoldPnl={stats.buy_and_hold_pnl}
              alpha={stats.alpha}
            />
            {stats.walk_forward && <WalkForwardTable data={stats.walk_forward} />}
            {stats.monte_carlo && (
              <MonteCarloPanel
                data={stats.monte_carlo}
                initialCapital={stats.initial_capital}
                nTrades={stats.total_trades}
              />
            )}
            {trades.length > 0 && (
              <TradesStatsPanel trades={trades} />
            )}
            {trades.length > 0 && (
              <Card>
                <CardHeader
                  className="cursor-pointer"
                  onClick={() => setCollapsedTrades((m) => ({ ...m, [name]: !tradesClosed }))}
                >
                  <CardTitle className="text-sm flex items-center gap-2">
                    Trades ({trades.length})
                    <span className="ml-auto">
                      {tradesClosed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
                    </span>
                  </CardTitle>
                </CardHeader>
                {tradesClosed ? null : (
                  <CardContent className="p-0">
                    <TradesTable
                      trades={trades}
                      meta={{
                        symbol: r?.symbol ?? '',
                        timeframe: r?.timeframe ?? '',
                        strategy: name,
                      }}
                    />
                  </CardContent>
                )}
              </Card>
            )}

            {idx === 0 && (
              <>
                <CostBreakdownCard result={r} />
                <CostSimulatorPanel
                  symbol={r?.symbol ?? ''}
                  timeframe={r?.timeframe ?? ''}
                  strategies={strategies.map(([n]) => n).join(',')}
                  limit={r?.limit ?? r?.n_bars ?? 500}
                  currentCostModel={r?.cost_model}
                />
              </>
            )}

            <DiagnosticsPanel diagnostics={normalizeDiagnostics(stats?.diagnostics ?? r?.diagnostics)} />

            {isMl && (
              <MLBacktestPanel
                mlInfo={stats?.ml_info}
                strategy={name}
                nTrades={stats?.total_trades ?? 0}
              />
            )}
            <TradesScatter trades={trades} symbol={r?.symbol} />
          </div>
        );
      })}
    </div>
  );
}

