'use client';

/**
 * QW-5 — Panneau de recommandations post-backtest.
 *
 * Affiche les recommandations produites par `app/engine/recommendations.py`
 * (12 règles : échantillon, PnL, outliers, frais, Sharpe, DD, alpha, win-rate,
 * borrow, régimes, points forts). Les recommandations sont ordonnées par
 * sévérité (critical > warning > info > positive) avec badges colorés et
 * liens d'action cliquables.
 *
 * Schéma consommé (cf. frontend/src/types/index.ts) :
 *   {
 *     recommendations: BacktestRecommendation[],
 *     recommendations_summary: {
 *       counts: { critical, warning, info, positive },
 *       verdict: 'critical' | 'risky' | 'neutral' | 'positive',
 *       verdict_label: string,
 *       total: number,
 *     }
 *   }
 *
 * Si `recommendations` est vide ou absent, le panneau ne s'affiche pas.
 */

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type {
  BacktestRecommendation,
  BacktestRecommendationsSummary,
} from '@/types';
import {
  AlertOctagon, AlertTriangle, Info, CheckCircle2, ExternalLink,
  ChevronDown, ChevronUp,
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface Props {
  recommendations?: BacktestRecommendation[];
  summary?: BacktestRecommendationsSummary;
  /** Nom de la stratégie (affiché dans le titre). */
  strategy?: string;
  className?: string;
  /** Replié par défaut (cartes KPI). */
  defaultCollapsed?: boolean;
}

// ── Mapping sévérité → { icon, color, badge variant } ────────────────────────
const SEVERITY_CONFIG: Record<
  BacktestRecommendation['severity'],
  { icon: typeof AlertOctagon; color: string; badge: 'danger' | 'warning' | 'muted' | 'success'; label: string }
> = {
  critical: {
    icon: AlertOctagon,
    color: 'text-red-400',
    badge: 'danger',
    label: 'Critique',
  },
  warning: {
    icon: AlertTriangle,
    color: 'text-amber-400',
    badge: 'warning',
    label: 'Attention',
  },
  info: {
    icon: Info,
    color: 'text-blue-400',
    badge: 'muted',
    label: 'Info',
  },
  positive: {
    icon: CheckCircle2,
    color: 'text-emerald-400',
    badge: 'success',
    label: 'Point fort',
  },
};

// ── Mapping verdict → couleur du header ──────────────────────────────────────
const VERDICT_BORDER: Record<BacktestRecommendationsSummary['verdict'], string> = {
  critical: 'border-l-4 border-l-red-500',
  risky: 'border-l-4 border-l-amber-500',
  neutral: 'border-l-4 border-l-blue-500',
  positive: 'border-l-4 border-l-emerald-500',
};

export function RecommendationsPanel({
  recommendations,
  summary,
  strategy,
  className,
  defaultCollapsed = false,
}: Props) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  // Ne pas afficher le panneau si aucune recommandation
  if (!recommendations || recommendations.length === 0) {
    return null;
  }

  const verdict = summary?.verdict ?? 'neutral';
  const verdictLabel = summary?.verdict_label ?? 'Analyse indisponible';
  const counts = summary?.counts ?? { critical: 0, warning: 0, info: 0, positive: 0 };
  const verdictBorder = VERDICT_BORDER[verdict] ?? VERDICT_BORDER.neutral;

  return (
    <Card className={cn(className, verdictBorder)}>
      <CardHeader
        className="cursor-pointer flex-col items-stretch gap-1"
        onClick={() => setCollapsed((c) => !c)}
      >
        <CardTitle className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            💡 Recommandations
            {strategy && (
              <span className="text-[10px] font-normal text-muted uppercase tracking-wider">
                {strategy}
              </span>
            )}
          </span>
          <span className="flex items-center gap-2">
          <Badge variant={
            verdict === 'critical' ? 'danger'
            : verdict === 'risky' ? 'warning'
            : verdict === 'positive' ? 'success'
            : 'muted'
          }>
            {verdictLabel}
          </Badge>
          {collapsed ? <ChevronDown className="w-4 h-4 text-muted" /> : <ChevronUp className="w-4 h-4 text-muted" />}
          </span>
        </CardTitle>
        {/* Compteurs synthétiques par sévérité */}
        <div className="flex items-center gap-3 text-[11px]">
          {counts.critical > 0 && (
            <span className="flex items-center gap-1 text-red-400">
              <AlertOctagon className="w-3 h-3" />
              {counts.critical} critique{counts.critical > 1 ? 's' : ''}
            </span>
          )}
          {counts.warning > 0 && (
            <span className="flex items-center gap-1 text-amber-400">
              <AlertTriangle className="w-3 h-3" />
              {counts.warning} avert{counts.warning > 1 ? 'issements' : 'issement'}
            </span>
          )}
          {counts.info > 0 && (
            <span className="flex items-center gap-1 text-blue-400">
              <Info className="w-3 h-3" />
              {counts.info} info{counts.info > 1 ? 's' : ''}
            </span>
          )}
          {counts.positive > 0 && (
            <span className="flex items-center gap-1 text-emerald-400">
              <CheckCircle2 className="w-3 h-3" />
              {counts.positive} point{counts.positive > 1 ? 's' : ''} fort{counts.positive > 1 ? 's' : ''}
            </span>
          )}
        </div>
      </CardHeader>
      {collapsed ? null : (
      <CardContent className="space-y-2">
        {recommendations.map((reco, idx) => {
          const cfg = SEVERITY_CONFIG[reco.severity] ?? SEVERITY_CONFIG.info;
          const Icon = cfg.icon;
          return (
            <div
              key={`${reco.code}-${idx}`}
              className={cn(
                'flex items-start gap-3 p-2 rounded-md border border-border/50',
                'hover:bg-card-hover/30 transition-colors',
              )}
            >
              {/* Icône sévérité */}
              <Icon className={cn('w-4 h-4 mt-0.5 flex-shrink-0', cfg.color)} />

              {/* Contenu */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium">{reco.title}</span>
                  <Badge variant={cfg.badge} className="text-[10px]">
                    {cfg.label}
                  </Badge>
                  <span className="text-[10px] text-muted-foreground font-mono">
                    {reco.code}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                  {reco.message}
                </p>
                {reco.action && (
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <span className="text-[11px] text-cyan-400">→</span>
                    {reco.action_link ? (
                      <a
                        href={reco.action_link}
                        className="text-[11px] text-cyan-400 hover:text-cyan-300 inline-flex items-center gap-1"
                      >
                        {reco.action}
                        <ExternalLink className="w-2.5 h-2.5" />
                      </a>
                    ) : (
                      <span className="text-[11px] text-cyan-400">{reco.action}</span>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </CardContent>
      )}
    </Card>
  );
}
