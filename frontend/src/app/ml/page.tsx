'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn, timeAgo, formatDateTime } from '@/lib/utils';
import { useMLStrategyInfo, useCandlesStats } from '@/hooks/use-api';
import { MLRecipesList } from '@/components/cards/ml-recipes-list';
import {
  Loader2, BrainCircuit, Database, CheckCircle2, XCircle,
  AlertCircle, Cpu,
} from 'lucide-react';
import type { MLStrategyInfo } from '@/types';

// ── Strategy table ──────────────────────────────────────────────────────────

function StrategyTable({ strategies }: { strategies: Record<string, MLStrategyInfo> }) {
  const entries = Object.entries(strategies || {});
  if (entries.length === 0) {
    return <div className="text-sm text-muted text-center py-6">Aucune stratégie ML</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-dim border-b border-border">
            <th className="p-3 font-medium">Stratégie</th>
            <th className="p-3 font-medium">Statut</th>
            <th className="p-3 font-medium text-right">Best AUC</th>
            <th className="p-3 font-medium text-right">Prochain retrain</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([name, info]) => {
            const trained = !!info?.is_trained;
            const auc = info?.best_auc ?? 0;
            const aucQuality = auc >= 0.7 ? 'text-emerald-400' : auc >= 0.55 ? 'text-amber-400' : 'text-red-400';
            return (
              <tr key={name} className="border-b border-border/30 hover:bg-card-hover">
                <td className="p-3">
                  <div className="flex items-center gap-2">
                    <BrainCircuit className="w-4 h-4 text-purple-400 flex-shrink-0" />
                    <span className="font-mono font-semibold">{name}</span>
                  </div>
                </td>
                <td className="p-3">
                  {trained ? (
                    <Badge variant="success">
                      <CheckCircle2 className="w-3 h-3" />
                      Entraîné
                    </Badge>
                  ) : (
                    <Badge variant="danger">
                      <XCircle className="w-3 h-3" />
                      Non entraîné
                    </Badge>
                  )}
                </td>
                <td className={cn('p-3 text-right font-mono font-semibold', aucQuality)}>
                  {auc > 0 ? auc.toFixed(4) : '—'}
                </td>
                <td className="p-3 text-right text-xs text-muted font-mono">
                  {info?.next_retrain_at ? timeAgo(info.next_retrain_at) : 'jamais'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Candles cache stats ─────────────────────────────────────────────────────

function CandlesStatsTable({ store }: { store: any }) {
  // Expecting store as Record<symbol, Record<tf, {count, first, last, size_bytes?}>>
  const symbols = Object.entries(store || {});
  if (symbols.length === 0) {
    return <div className="text-sm text-muted text-center py-6">Cache bougies vide</div>;
  }
  const rows: Array<{ symbol: string; tf: string; count: number; first: string; last: string; size: number }> = [];
  for (const [symbol, tfs] of symbols) {
    for (const [tf, info] of Object.entries(tfs || {})) {
      const i = info as any;
      rows.push({
        symbol,
        tf,
        count: i?.count ?? 0,
        first: i?.first ?? '—',
        last: i?.last ?? '—',
        size: i?.size_bytes ?? 0,
      });
    }
  }
  rows.sort((a, b) => a.symbol.localeCompare(b.symbol) || a.tf.localeCompare(b.tf));

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-dim border-b border-border">
            <th className="p-3 font-medium">Symbole</th>
            <th className="p-3 font-medium">TF</th>
            <th className="p-3 font-medium text-right">Bougies</th>
            <th className="p-3 font-medium">Première</th>
            <th className="p-3 font-medium">Dernière</th>
            <th className="p-3 font-medium text-right">Taille</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.symbol}-${r.tf}`} className="border-b border-border/30 hover:bg-card-hover">
              <td className="p-3 font-mono font-semibold">{r.symbol}</td>
              <td className="p-3"><Badge variant="purple">{r.tf}</Badge></td>
              <td className="p-3 text-right font-mono">{r.count.toLocaleString()}</td>
              <td className="p-3 text-xs text-muted font-mono">{formatDateTime(r.first)}</td>
              <td className="p-3 text-xs text-muted font-mono">{formatDateTime(r.last)}</td>
              <td className="p-3 text-right font-mono text-muted">
                {r.size > 0 ? formatBytes(r.size) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function MLPage() {
  const { data: mlData, isLoading: mlLoading, isError: mlError } = useMLStrategyInfo();
  const { data: candlesData, isLoading: candlesLoading, isError: candlesError } = useCandlesStats();

  const strategies = (mlData?.strategies || {}) as Record<string, MLStrategyInfo>;
  const trainedCount = Object.values(strategies).filter((s) => s?.is_trained).length;
  const totalCount = Object.keys(strategies).length;

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Modèles ML</h1>
          <p className="text-sm text-muted mt-1">
            Gestion des modèles machine learning et cache des bougies
          </p>
        </div>
        <Badge variant={trainedCount === totalCount && totalCount > 0 ? 'success' : 'warning'}>
          <BrainCircuit className="w-3 h-3" />
          {trainedCount}/{totalCount} entraînés
        </Badge>
      </div>

      {/* ML strategy info */}
      <Card>
        <CardHeader>
          <CardTitle>Stratégies ML</CardTitle>
          <Cpu className="w-4 h-4 text-purple-400" />
        </CardHeader>
        <CardContent className="p-0">
          {mlLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
            </div>
          ) : mlError ? (
            <div className="text-center py-8 text-red-400 text-sm">
              <AlertCircle className="w-8 h-8 mx-auto mb-2" />
              Erreur lors du chargement des stratégies ML
            </div>
          ) : (
            <StrategyTable strategies={strategies} />
          )}
        </CardContent>
      </Card>

      {/* Candles cache */}
      <Card>
        <CardHeader>
          <CardTitle>Cache bougies (datasets)</CardTitle>
          <Database className="w-4 h-4 text-primary-400" />
        </CardHeader>
        <CardContent className="p-0">
          {candlesLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
            </div>
          ) : candlesError ? (
            <div className="text-center py-8 text-red-400 text-sm">
              <AlertCircle className="w-8 h-8 mx-auto mb-2" />
              Erreur lors du chargement du cache
            </div>
          ) : (
            <CandlesStatsTable store={candlesData?.store} />
          )}
        </CardContent>
      </Card>

      {/* S9-F3-US1 — Recettes ML disponibles */}
      <MLRecipesList />

      {/* Info card */}
      <Card>
        <CardContent>
          <div className="flex items-start gap-3 text-sm text-muted">
            <AlertCircle className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-foreground mb-1">Lecture seule</p>
              <p>
                Cette page est en lecture seule. Les modèles ML sont entraînés et retrainés
                automatiquement par le backend selon la configuration du scheduler. L&apos;AUC reflète
                la qualité de discrimination du modèle (0.5 = aléatoire, 1.0 = parfait).
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
