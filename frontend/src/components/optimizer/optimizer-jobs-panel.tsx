'use client';

import { useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, formatUSD } from '@/lib/utils';
import {
  Loader2, XCircle, Zap, ChevronDown, ChevronRight, FileDown, GitCompare,
} from 'lucide-react';
import type { OptimizeJob } from '@/types';
import { OptimizerHistory } from '@/components/cards/optimizer-history';
import { JobCard } from '@/components/optimizer/job-card';
import { STATUS_LABEL } from '@/components/optimizer/status';

export function OptimizerJobsPanel({
  jobs,
  jobsLoading,
  jobsError,
  filterMl,
}: {
  jobs: OptimizeJob[];
  jobsLoading: boolean;
  jobsError: string | null;
  filterMl: boolean;
}) {
  const [allExpanded, setAllExpanded] = useState(false);
  const [jobsSearch, setJobsSearch] = useState('');
  const [jobsStatusFilter, setJobsStatusFilter] = useState('');
  const [compareMode, setCompareMode] = useState(false);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  const toggleCompare = (jobId: string) => {
    setCompareIds((prev) =>
      prev.includes(jobId)
        ? prev.filter((id) => id !== jobId)
        : prev.length < 4 ? [...prev, jobId] : prev,
    );
  };

  const filteredJobs = jobs.filter((j) => {
    if (jobsStatusFilter && j.status !== jobsStatusFilter) return false;
    if (jobsSearch) {
      const q = jobsSearch.toLowerCase();
      if (!j.strategy?.toLowerCase().includes(q)
        && !j.job_id?.toLowerCase().includes(q)
        && !j.symbol?.toLowerCase().includes(q)
        && !j.timeframe?.toLowerCase().includes(q)) return false;
    }
    return true;
  });
  const compareJobs = filteredJobs.filter((j) => compareIds.includes(j.job_id));

  const jobGroups = useMemo(() => {
    const groups: Array<{ key: string; label: string; jobs: OptimizeJob[]; defaultExpanded: boolean }> = [
      { key: 'running', label: 'En cours', jobs: [], defaultExpanded: true },
      { key: 'error', label: 'Erreurs', jobs: [], defaultExpanded: true },
      { key: 'cancelled', label: 'Annulés', jobs: [], defaultExpanded: false },
      { key: 'done', label: 'Terminés', jobs: [], defaultExpanded: false },
    ];
    const map: Record<string, typeof groups[number]> = Object.fromEntries(groups.map((g) => [g.key, g]));
    for (const j of filteredJobs) {
      const g = map[j.status] ?? map.done;
      g.jobs.push(j);
    }
    return groups.filter((g) => g.jobs.length > 0);
  }, [filteredJobs]);

  const toggleGroup = (key: string) =>
    setCollapsedGroups((s) => ({ ...s, [key]: !s[key] }));

  const exportJobsCsv = () => {
    const headers = ['job_id', 'strategy', 'timeframe', 'symbol', 'status', 'progress', 'best_score', 'trials_done', 'n_trials', 'method', 'applied'];
    const rows = filteredJobs.map((j) => [
      j.job_id, j.strategy, j.timeframe, j.symbol ?? '', j.status,
      j.progress, j.best_score, j.trials_done, j.n_trials, j.method, j.applied ? 'oui' : 'non',
    ]);
    const csv = [headers.join(','), ...rows.map((r) => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `optimizer-jobs-${new Date().toISOString().slice(0, 19)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">Jobs</h2>
        <div className="flex items-center gap-3">
          {filteredJobs.length > 0 && (
            <Button size="sm" variant="ghost" onClick={exportJobsCsv} className="h-7 text-xs" title="Exporter les jobs en CSV">
              <FileDown className="w-3 h-3" />
              CSV
            </Button>
          )}
          {filteredJobs.length >= 2 && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => { setCompareMode(!compareMode); if (compareMode) setCompareIds([]); }}
              className={cn('h-7 text-xs', compareMode && 'text-cyan-400')}
              title="Comparer les jobs côte à côte (max 4)"
            >
              <GitCompare className="w-3 h-3" />
              {compareMode ? `Annuler (${compareIds.length})` : 'Comparer'}
            </Button>
          )}
          {filteredJobs.length > 0 && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setAllExpanded((v) => !v)}
              className="h-7 text-xs"
              aria-label={allExpanded ? 'Réduire toutes les cartes' : 'Déplier toutes les cartes'}
            >
              {allExpanded ? 'Réduire tout' : 'Tout ouvrir'}
            </Button>
          )}
          <span className="text-xs text-dim font-mono">
            {filteredJobs.length}{jobsSearch || jobsStatusFilter ? `/${jobs.length}` : ''}
          </span>
        </div>
      </div>

      {jobs.length > 3 && (
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <input
            type="text"
            value={jobsSearch}
            onChange={(e) => setJobsSearch(e.target.value)}
            placeholder="Rechercher (stratégie, symbole, job_id…)"
            className="px-3 py-1.5 bg-card-hover border border-border rounded-md text-xs w-56"
          />
          <div className="flex items-center gap-1">
            {['', 'running', 'done', 'error', 'cancelled'].map((s) => (
              <button
                key={s}
                onClick={() => setJobsStatusFilter(s)}
                className={cn(
                  'text-[10px] px-2 py-0.5 rounded-md border transition-colors',
                  jobsStatusFilter === s
                    ? 'bg-primary-500/10 border-primary-500/30 text-primary-400'
                    : 'bg-card-hover/30 border-border text-muted hover:text-foreground',
                )}
              >
                {s === '' ? 'Tous' : STATUS_LABEL[s] ?? s}
              </button>
            ))}
          </div>
        </div>
      )}

      {jobsLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
        </div>
      ) : jobsError ? (
        <Card>
          <CardContent className="text-center py-8 text-red-400 text-sm">
            <XCircle className="w-8 h-8 mx-auto mb-2" />
            {jobsError}
          </CardContent>
        </Card>
      ) : jobs.length === 0 || jobGroups.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12 text-muted text-sm">
            <Zap className="w-8 h-8 mx-auto mb-2 text-dim" />
            Aucun job d&apos;optimisation. Lancez-en un ci-dessus.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {jobGroups.map((g) => {
            const isCollapsed = collapsedGroups[g.key] ?? !g.defaultExpanded;
            return (
              <div key={g.key}>
                <button
                  type="button"
                  onClick={() => toggleGroup(g.key)}
                  className="w-full flex items-center gap-2 py-1.5 text-xs uppercase tracking-wider text-muted hover:text-foreground"
                  aria-expanded={!isCollapsed}
                >
                  {isCollapsed
                    ? <ChevronRight className="w-3.5 h-3.5" />
                    : <ChevronDown className="w-3.5 h-3.5" />}
                  <span className="font-semibold">{g.label}</span>
                  <Badge variant="default" className="text-[0.55rem]">{g.jobs.length}</Badge>
                </button>
                {!isCollapsed && (
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-2">
                    {g.jobs.map((job) => (
                      <JobCard
                        key={job.job_id}
                        job={job}
                        defaultExpanded={allExpanded || g.key === 'running'}
                        filterMl={filterMl}
                        compareMode={compareMode}
                        compareSelected={compareIds.includes(job.job_id)}
                        onCompareToggle={toggleCompare}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {compareMode && compareJobs.length >= 2 && (
        <Card className="border-cyan-500/30 mt-4">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <GitCompare className="w-4 h-4 text-cyan-400" />
              Comparaison ({compareJobs.length}/4)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-dim border-b border-border">
                    <th className="p-2">Métrique</th>
                    {compareJobs.map((j) => (
                      <th key={j.job_id} className="p-2 text-right font-mono">
                        {j.strategy}<span className="text-dim">@{j.timeframe}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-border/30">
                    <td className="p-2 text-muted">OOS Score</td>
                    {compareJobs.map((j) => (
                      <td key={j.job_id} className="p-2 text-right font-mono text-emerald-400">
                        {(j.result?.best_oos_score ?? 0).toFixed(4)}
                      </td>
                    ))}
                  </tr>
                  <tr className="border-b border-border/30">
                    <td className="p-2 text-muted">OOS PnL</td>
                    {compareJobs.map((j) => (
                      <td key={j.job_id} className="p-2 text-right font-mono">
                        {formatUSD(j.result?.best_oos_pnl ?? 0)}
                      </td>
                    ))}
                  </tr>
                  <tr className="border-b border-border/30">
                    <td className="p-2 text-muted">Trades</td>
                    {compareJobs.map((j) => (
                      <td key={j.job_id} className="p-2 text-right font-mono">
                        {j.result?.best_oos_trades ?? 0}
                      </td>
                    ))}
                  </tr>
                  <tr className="border-b border-border/30">
                    <td className="p-2 text-muted">Sharpe</td>
                    {compareJobs.map((j) => (
                      <td key={j.job_id} className="p-2 text-right font-mono">
                        {(j.result?.best_oos_sharpe ?? 0).toFixed(2)}
                      </td>
                    ))}
                  </tr>
                  <tr className="border-b border-border/30">
                    <td className="p-2 text-muted">Overfit</td>
                    {compareJobs.map((j) => (
                      <td key={j.job_id} className={`p-2 text-right font-mono ${(j.result?.overfit ?? 0) > 2.5 ? 'text-amber-400' : ''}`}>
                        {(j.result?.overfit ?? 0).toFixed(2)}
                      </td>
                    ))}
                  </tr>
                  {compareJobs.some((j) => j.deflated_sharpe != null) && (
                    <tr className="border-b border-border/30">
                      <td className="p-2 text-muted">Deflated Sharpe</td>
                      {compareJobs.map((j) => (
                        <td key={j.job_id} className="p-2 text-right font-mono">
                          {j.deflated_sharpe != null ? `${(j.deflated_sharpe * 100).toFixed(1)}%` : '—'}
                        </td>
                      ))}
                    </tr>
                  )}
                  {compareJobs.some((j) => j.wf_consistency != null) && (
                    <tr className="border-b border-border/30">
                      <td className="p-2 text-muted">WF Consistency</td>
                      {compareJobs.map((j) => (
                        <td key={j.job_id} className="p-2 text-right font-mono">
                          {j.wf_consistency != null ? `${j.wf_consistency.toFixed(0)}%` : '—'}
                        </td>
                      ))}
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      <OptimizerHistory />
    </div>
  );
}
