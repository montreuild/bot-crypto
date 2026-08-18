'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatDateTime } from '@/lib/utils';
import { toast } from 'sonner';
import { useDataStatus, useRefetchData } from '@/hooks/use-api';
import { api } from '@/lib/api';
import {
  Loader2, RefreshCw, Database, AlertCircle, ExternalLink, HardDrive,
  TrendingUp, Play, CheckCircle2, XCircle,
} from 'lucide-react';
import { TimeframeButtons } from '@/components/ui/timeframe-select';

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

interface DatasetRow {
  symbol: string;
  tf: string;
  count: number;
  first: string;
  last: string;
  size: number;
}

interface DatasetInfo {
  symbol?: string;
  tf?: string;
  timeframe?: string;
  bars?: number;
  count?: number;
  from?: string;
  first?: string;
  to?: string;
  last?: string;
  size_bytes?: number;
  size_kb?: number;
}

interface DataStatusPayload {
  datasets?: DatasetInfo[];
  store?: Record<string, Record<string, DatasetInfo>>;
}

function flattenDatasets(data?: DataStatusPayload | DatasetInfo[] | null): DatasetRow[] {
  if (!data) return [];
  const mapRow = (d: DatasetInfo): DatasetRow => ({
    symbol: d.symbol ?? '',
    tf: d.timeframe || d.tf || '',
    count: Number(d.bars ?? d.count ?? 0),
    first: d.from ?? d.first ?? '',
    last: d.to ?? d.last ?? '',
    size: d.size_bytes != null
      ? Number(d.size_bytes)
      : Math.round(Number(d.size_kb ?? 0) * 1024),
  });
  if (!Array.isArray(data) && Array.isArray(data.datasets)) {
    return data.datasets.map(mapRow);
  }
  if (!Array.isArray(data) && data.store && typeof data.store === 'object') {
    const rows: DatasetRow[] = [];
    for (const [symbol, tfs] of Object.entries(data.store)) {
      for (const [tf, info] of Object.entries(tfs)) {
        rows.push(mapRow({ symbol, tf, ...info }));
      }
    }
    return rows;
  }
  if (Array.isArray(data)) {
    return data.map(mapRow);
  }
  return [];
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function DataPage() {
  const { data, isLoading, isError, refetch } = useDataStatus();
  const refetchData = useRefetchData();

  const [manualSymbol, setManualSymbol] = useState('BTC/USDC');
  const [manualTf, setManualTf] = useState('1h');

  // ── Backfill des actions (S5 — équivalent du bouton Jinja2 /data) ────────
  const [backfillTf, setBackfillTf] = useState('1d');
  const [backfillYears, setBackfillYears] = useState(20);
  const [backfillJob, setBackfillJob] = useState<{
    status?: string;
    error?: string | null;
    progress?: { done?: number; total?: number; current_symbol?: string | null };
    results?: Array<{ symbol: string; tf: string; bars: number; ok: boolean; error?: string }>;
  } | null>(null);
  const [backfillPolling, setBackfillPolling] = useState(false);

  const pollBackfill = useCallback(async (jobId: string) => {
    try {
      const status = await api.getBackfillStatus(jobId);
      setBackfillJob(status);
      if (status.status === 'started') {
        // Continue polling toutes les 2s
        setTimeout(() => pollBackfill(jobId), 2000);
      } else {
        setBackfillPolling(false);
        if (status.status === 'done') {
          const ok = status.results.filter((r) => r.ok).length;
          toast.success(`Backfill terminé : ${ok}/${status.results.length} symboles`);
        } else if (status.status === 'error') {
          toast.error(`Backfill échoué : ${status.error}`);
        }
      }
    } catch (e: any) {
      setBackfillPolling(false);
      toast.error(`Suivi backfill échoué : ${e.message}`);
    }
  }, []);

  const handleStartBackfill = async () => {
    try {
      const res = await api.startBackfillEquities(backfillTf, backfillYears);
      setBackfillJob(res);
      setBackfillPolling(true);
      toast.info(
        `Backfill démarré : ${res.univers.join(', ')} — TF ${res.tf}, ${res.years} ans`,
      );
      // Démarre le polling
      setTimeout(() => pollBackfill(res.job_id), 2000);
    } catch (e: any) {
      toast.error(`Backfill échoué : ${e.message}`);
    }
  };

  const datasets = flattenDatasets(data);
  const totalBars = datasets.reduce((s, d) => s + (d.count || 0), 0);
  const totalSize = datasets.reduce((s, d) => s + (d.size || 0), 0);

  const handleRefetch = async (symbol: string, tf: string) => {
    try {
      toast.info(`Refetch ${symbol} ${tf}...`);
      await refetchData.mutateAsync({ symbol, tf });
      toast.success(`Refetch terminé: ${symbol} ${tf}`);
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const handleManualRefetch = async () => {
    if (!manualSymbol.trim() || !manualTf.trim()) {
      toast.error('Symbole et TF requis');
      return;
    }
    await handleRefetch(manualSymbol.trim(), manualTf.trim());
  };

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Données OHLCV</h1>
          <p className="text-sm text-muted mt-1">
            Gestion du cache de bougies · {datasets.length} datasets · {totalBars.toLocaleString()} bars · {formatBytes(totalSize)}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => refetch()}
          disabled={isLoading}
        >
          <RefreshCw className={cn('w-3.5 h-3.5', isLoading && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      {/* Manual refetch form */}
      <Card>
        <CardHeader>
          <CardTitle>Refetch manuel</CardTitle>
          <HardDrive className="w-4 h-4 text-primary-400" />
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
            <div>
              <label className="text-xs text-dim block mb-1.5">Symbole</label>
              <input
                aria-label="Symbole"
                value={manualSymbol}
                onChange={(e) => setManualSymbol(e.target.value)}
                placeholder="BTC/USDC"
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">Timeframe</label>
              <TimeframeButtons
                value={manualTf}
                onChange={setManualTf}
                size="sm"
                allowExtra={['1m', '5m']}
              />
            </div>
            <Button
              onClick={handleManualRefetch}
              disabled={refetchData.isPending}
              variant="primary"
            >
              {refetchData.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <RefreshCw className="w-4 h-4" />
              )}
              Refetch
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* ── Backfill des actions (S5 — équivalent du bouton Jinja2 /data) ──── */}
      <Card>
        <CardHeader>
          <CardTitle>Backfill des actions (yfinance)</CardTitle>
          <TrendingUp className="w-4 h-4 text-primary-400" />
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <p className="text-sm text-muted">
              Peuple le cache OHLCV des actions des univers activés dans
              <code className="ml-1 text-xs bg-card-hover px-1 py-0.5 rounded">scanner.universe</code>
              (ex. <code className="text-xs bg-card-hover px-1 py-0.5 rounded">sbf120</code>).
              Lance en arrière-plan — suit l&apos;avancement ci-dessous.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
              <div>
                <label className="text-xs text-dim block mb-1.5">Timeframe</label>
                <TimeframeButtons value={backfillTf} onChange={setBackfillTf} size="sm" />
                <p className="text-[10px] text-dim mt-1">1d recommandé (Yahoo LTF ≈ 88 bougies)</p>
              </div>
              <div>
                <label className="text-xs text-dim block mb-1.5">Années d&apos;historique</label>
                <input
                  aria-label="Années d&apos;historique"
                  type="number"
                  value={backfillYears}
                  onChange={(e) => setBackfillYears(Number(e.target.value))}
                  min={1}
                  max={20}
                  className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm"
                />
              </div>
              <Button
                onClick={handleStartBackfill}
                disabled={backfillPolling}
                variant="primary"
              >
                {backfillPolling ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Play className="w-4 h-4" />
                )}
                {backfillPolling ? 'Backfill en cours...' : 'Lancer le backfill'}
              </Button>
            </div>

            {/* Avancement du backfill */}
            {backfillJob && (
              <div className="border border-border rounded-lg p-4 bg-card-hover space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {backfillJob.status === 'started' && (
                      <Loader2 className="w-4 h-4 text-primary-400 animate-spin" />
                    )}
                    {backfillJob.status === 'done' && (
                      <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                    )}
                    {backfillJob.status === 'error' && (
                      <XCircle className="w-4 h-4 text-red-400" />
                    )}
                    <span className="font-medium">
                      Job {backfillJob.job_id} — {backfillJob.status}
                    </span>
                  </div>
                  <Badge variant="info">
                    {backfillJob.progress.done}/{backfillJob.progress.total || '?'}
                  </Badge>
                </div>
                {backfillJob.progress.current_symbol && (
                  <div className="text-xs text-muted font-mono">
                    En cours : {backfillJob.progress.current_symbol}
                  </div>
                )}
                {backfillJob.univers && (
                  <div className="text-xs text-dim">
                    Univers : {backfillJob.univers.join(', ')} — TF {backfillJob.tf}, {backfillJob.years} ans
                  </div>
                )}
                {/* Barre de progression */}
                {backfillJob.progress.total > 0 && (
                  <div className="w-full bg-card rounded-full h-1.5 overflow-hidden">
                    <div
                      className="h-full bg-primary-400 transition-all"
                      style={{
                        width: `${(backfillJob.progress.done / backfillJob.progress.total) * 100}%`,
                      }}
                    />
                  </div>
                )}
                {/* Stats finales */}
                {backfillJob.status === 'done' && backfillJob.results && (
                  <div className="text-xs text-muted">
                    <span className="text-emerald-400 font-medium">
                      {backfillJob.results.filter((r: any) => r.ok).length} OK
                    </span>
                    {' · '}
                    <span className="text-red-400 font-medium">
                      {backfillJob.results.filter((r: any) => !r.ok).length} échoués
                    </span>
                    {' · '}
                    <span>
                      {backfillJob.results.reduce((s: number, r: any) => s + r.bars, 0).toLocaleString()} bougies
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Datasets table */}
      <Card>
        <CardHeader>
          <CardTitle>Datasets en cache</CardTitle>
          <Database className="w-4 h-4 text-primary-400" />
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
            </div>
          ) : isError ? (
            <div className="text-center py-8 text-red-400 text-sm">
              <AlertCircle className="w-8 h-8 mx-auto mb-2" />
              Erreur lors du chargement
            </div>
          ) : datasets.length === 0 ? (
            <div className="text-center py-12 text-muted text-sm">
              Aucun dataset en cache
            </div>
          ) : (
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
                    <th className="p-3 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {datasets
                    .sort((a, b) => a.symbol.localeCompare(b.symbol) || a.tf.localeCompare(b.tf))
                    .map((d) => (
                      <tr key={`${d.symbol}-${d.tf}`} className="border-b border-border/30 hover:bg-card-hover">
                        <td className="p-3 font-mono font-semibold">{d.symbol}</td>
                        <td className="p-3"><Badge variant="purple">{d.tf}</Badge></td>
                        <td className="p-3 text-right font-mono">{d.count.toLocaleString()}</td>
                        <td className="p-3 text-xs text-muted font-mono">{formatDateTime(d.first)}</td>
                        <td className="p-3 text-xs text-muted font-mono">{formatDateTime(d.last)}</td>
                        <td className="p-3 text-right font-mono text-muted">{formatBytes(d.size)}</td>
                        <td className="p-3">
                          <div className="flex items-center justify-end gap-1.5">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => handleRefetch(d.symbol, d.tf)}
                              disabled={refetchData.isPending}
                              title={`Refetch ${d.symbol} ${d.tf}`}
                            >
                              <RefreshCw className={cn('w-3 h-3', refetchData.isPending && 'animate-spin')} />
                            </Button>
                            <Link
                              href={`/market?tab=scanner&symbol=${encodeURIComponent(d.symbol)}&tf=${encodeURIComponent(d.tf)}`}
                              className="inline-flex items-center justify-center h-8 px-2 rounded-md text-xs text-muted hover:text-primary-400 hover:bg-card-hover border border-border"
                              title="Analyser dans le scanner"
                            >
                              <ExternalLink className="w-3 h-3" />
                              Analyser
                            </Link>
                          </div>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Loading overlay during refetch */}
      {refetchData.isPending && (
        <div className="fixed bottom-6 right-6 z-40 bg-card border border-border rounded-lg p-3 shadow-xl flex items-center gap-3">
          <Loader2 className="w-4 h-4 text-primary-400 animate-spin" />
          <div className="text-sm">
            <div className="font-semibold">Refetch en cours</div>
            <div className="text-xs text-muted">Téléchargement des bougies...</div>
          </div>
        </div>
      )}
    </div>
  );
}
