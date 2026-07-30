'use client';

/**
 * S3-F1-US2 — Bandeau de santé en français courant.
 *
 * Synthétise l'état du portefeuille en une phrase compréhensible :
 * "Ton portefeuille a généré +2,3% cette semaine, porté par trend_rider
 * sur BTC/4h. 2 bots en essai, 0 en alerte."
 *
 * S'adapte au persona (débutant vs expert) via la prop `expert`.
 */

import { Card } from '@/components/ui/card';
import { TrendingUp, TrendingDown, AlertCircle, CheckCircle2, Activity } from 'lucide-react';
import { cn, formatUSD, formatPct } from '@/lib/utils';
import type { BotStatus } from '@/types';

interface HealthBannerProps {
  status: BotStatus | undefined;
  portfolio: any | undefined;
  expert?: boolean;
}

export function HealthBanner({ status, portfolio, expert = false }: HealthBannerProps) {
  // Cas : backend down ou pas encore de données
  if (!status) {
    return (
      <Card className="p-4 flex items-center gap-3 bg-card">
        <Activity className="w-5 h-5 text-muted animate-pulse" />
        <p className="text-sm text-muted">
          Récupération de l&apos;état du portefeuille…
        </p>
      </Card>
    );
  }

  const pnl = status.total_pnl ?? 0;
  const pnlPct = status.total_pnl_pct ?? 0;
  const isPositive = pnl >= 0;
  const isRunning = status.status === 'running';

  // Détection des alertes
  const hasCircuitBreaker = status.circuit_breaker_active;
  const dailyDdPct = Math.abs(status.daily_pnl_pct ?? 0);
  const dailyDdLimit = status.daily_dd_limit ?? 0.05;
  const dailyDdUsed = dailyDdPct / (dailyDdLimit * 100);
  const hasDailyDdAlert = dailyDdUsed > 0.7;

  // Comptage des bots par lifecycle
  const lifecycle = portfolio?.lifecycle || status.lifecycle;
  const counts = lifecycle?.counts || { candidat: 0, essai: 0, actif: 0, retire: 0 };

  // Top contributeur (best strategy by PnL)
  const byStrategy = status.by_strategy || {};
  const topStrategy = Object.entries(byStrategy)
    .sort(([, a]: any, [, b]: any) => (b.total_pnl ?? 0) - (a.total_pnl ?? 0))[0];
  const topStrategyName = topStrategy?.[0];
  const topStrategyPnl = topStrategy?.[1]?.total_pnl ?? 0;

  // Construction du message
  let message = '';
  let icon: React.ReactNode;
  let tone: 'positive' | 'negative' | 'neutral' | 'alert' = 'neutral';

  if (hasCircuitBreaker) {
    icon = <AlertCircle className="w-5 h-5 text-amber-400" />;
    tone = 'alert';
    message = `⚠️ Circuit breaker actif${status.circuit_breaker_reason ? ` — ${status.circuit_breaker_reason}` : ''}. Le trading est en pause.`;
  } else if (!isRunning) {
    icon = <Activity className="w-5 h-5 text-muted" />;
    tone = 'neutral';
    message = `Bot à l'arrêt. ${status.paper_mode ? 'Mode paper.' : 'Mode live.'} Démarre le trader pour reprendre.`;
  } else if (isPositive) {
    icon = <TrendingUp className="w-5 h-5 text-emerald-400" />;
    tone = 'positive';
    const parts = [`Ton portefeuille a généré ${formatUSD(pnl, { sign: true })} (${formatPct(pnlPct)})`];
    if (topStrategyName && topStrategyPnl > 0) {
      parts.push(`porté par ${topStrategyName}`);
    }
    parts.push(`${counts.essai || 0} bot${(counts.essai || 0) > 1 ? 's' : ''} en essai, ${hasDailyDdAlert ? '⚠️ attention drawdown' : '0 alerte'}.`);
    message = parts.join(' ');
  } else {
    icon = <TrendingDown className="w-5 h-5 text-red-400" />;
    tone = 'negative';
    const parts = [`Ton portefeuille est à ${formatUSD(pnl, { sign: true })} (${formatPct(pnlPct)})`];
    if (topStrategyName && topStrategyPnl < 0) {
      parts.push(`tiré vers le bas par ${topStrategyName}`);
    }
    parts.push(`${counts.actif || 0} bot${(counts.actif || 0) > 1 ? 's' : ''} actif${(counts.actif || 0) > 1 ? 's' : ''}.`);
    message = parts.join(' ');
  }

  const toneClasses = {
    positive: 'border-emerald-500/30 bg-emerald-500/5',
    negative: 'border-red-500/30 bg-red-500/5',
    neutral: 'border-border bg-card',
    alert: 'border-amber-500/40 bg-amber-500/5',
  };

  return (
    <Card className={cn('p-4 flex items-center gap-3', toneClasses[tone])}>
      <div className="flex-shrink-0">{icon}</div>
      <p className="text-sm flex-1 min-w-0">
        <span className={cn(
          'font-medium',
          tone === 'positive' && 'text-emerald-400',
          tone === 'negative' && 'text-red-400',
          tone === 'alert' && 'text-amber-400',
        )}>
          {status.paper_mode ? '📝 ' : '🔴 '}
        </span>
        <span className="text-foreground">{message}</span>
      </p>
      {expert && (
        <div className="text-xs text-dim font-mono flex-shrink-0">
          DD jour: {(dailyDdUsed * 100).toFixed(0)}% · CB: {hasCircuitBreaker ? 'ON' : 'off'}
        </div>
      )}
    </Card>
  );
}
