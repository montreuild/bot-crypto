'use client';

/**
 * Onglet Optimizer de `/lab` — optimisation bayésienne / grid / random.
 * Formulaire → `optimizer-config-form.tsx`. Jobs → `optimizer-jobs-panel.tsx`.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Activity } from 'lucide-react';
import {
  useOptimizeSpaces,
  useOptimizeResults,
  useOptimizeStatus,
} from '@/hooks/use-api';
import { ParamSpaceTable } from '@/components/optimizer/param-space-table';
import { OptimizerConfigForm } from '@/components/optimizer/optimizer-config-form';
import { OptimizerJobsPanel } from '@/components/optimizer/optimizer-jobs-panel';
import { normalizeOptimizeJobs } from '@/components/optimizer/optimizer-utils';

export function OptimizerView({ filterMl = false }: { filterMl?: boolean }) {
  const { data: spaces, isLoading: spacesLoading } = useOptimizeSpaces();
  const { data: resultsData } = useOptimizeResults();
  const {
    data: jobsData,
    isLoading: jobsLoading,
    isError: jobsIsError,
    error: jobsErrorObj,
  } = useOptimizeStatus();

  const jobs = normalizeOptimizeJobs(jobsData);
  const jobsError = jobsIsError
    ? (jobsErrorObj instanceof Error ? jobsErrorObj.message : 'Erreur de chargement')
    : null;
  const activeResults = resultsData?.active_per_tf || {};
  const running = jobs.filter((j) => j.status === 'running').length;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">
            {filterMl ? 'Optimiseur ML' : 'Optimiseur'}
          </h2>
          <p className="text-sm text-muted mt-1">
            {filterMl
              ? 'Optimisation bayésienne / grid / random des stratégies ML avec validation OOS et entraînement LightGBM'
              : 'Optimisation bayésienne / grid / random des stratégies avec validation OOS'}
          </p>
        </div>
        <Badge variant="info">
          <Activity className="w-3 h-3" />
          {running} en cours
        </Badge>
      </div>

      <OptimizerConfigForm filterMl={filterMl} spaces={spaces} />

      {!spacesLoading && <ParamSpaceTable spaces={spaces || {}} />}

      <Card>
        <CardHeader>
          <CardTitle>Résultats actifs par TF</CardTitle>
          <Badge variant="success">appliqués</Badge>
        </CardHeader>
        <CardContent>
          {Object.keys(activeResults).length === 0 ? (
            <div className="text-sm text-muted text-center py-6">Aucun résultat actif</div>
          ) : (
            <div className="space-y-2">
              {Object.entries(activeResults).map(([tf, strategies]) => (
                <div
                  key={tf}
                  className="flex items-center justify-between p-3 rounded-lg bg-card-hover border border-border"
                >
                  <span className="font-mono font-semibold text-sm">{tf}</span>
                  <div className="flex flex-wrap gap-1">
                    {strategies.map((s) => (
                      <Badge key={s} variant="info">{s}</Badge>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <OptimizerJobsPanel
        jobs={jobs}
        jobsLoading={jobsLoading}
        jobsError={jobsError}
        filterMl={filterMl}
      />
    </div>
  );
}
