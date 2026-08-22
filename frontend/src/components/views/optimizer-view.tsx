'use client';

/**
 * Onglet Optimizer de `/lab` — optimisation bayésienne / grid / random.
 * Formulaire → `optimizer-config-form.tsx`. Jobs → `optimizer-jobs-panel.tsx`.
 */

import { Badge } from '@/components/ui/badge';
import { Activity } from 'lucide-react';
import {
  useOptimizeSpaces,
  useOptimizeStatus,
} from '@/hooks/use-api';
import { ParamSpaceTable } from '@/components/optimizer/param-space-table';
import { OptimizerConfigForm } from '@/components/optimizer/optimizer-config-form';
import { OptimizerJobsPanel } from '@/components/optimizer/optimizer-jobs-panel';
import { normalizeOptimizeJobs } from '@/components/optimizer/optimizer-utils';

export function OptimizerView({ filterMl = false }: { filterMl?: boolean }) {
  const { data: spaces, isLoading: spacesLoading } = useOptimizeSpaces();
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

      {!spacesLoading && <ParamSpaceTable spaces={spaces || {}} filterMl={filterMl} />}

      <OptimizerJobsPanel
        jobs={jobs}
        jobsLoading={jobsLoading}
        jobsError={jobsError}
        filterMl={filterMl}
      />
    </div>
  );
}
