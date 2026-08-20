'use client';

import type { OptimizeJob } from '@/types';

export function normalizeOptimizeJobs(data: unknown): OptimizeJob[] {
  if (!data) return [];
  if (Array.isArray(data)) return data as OptimizeJob[];
  if (typeof data !== 'object') return [];
  const rec = data as Record<string, unknown>;
  if (Array.isArray(rec.jobs)) return rec.jobs as OptimizeJob[];
  if (typeof rec.job_id === 'string') return [data as OptimizeJob];
  return Object.entries(rec).map(([job_id, job]) => ({
    job_id,
    ...(job && typeof job === 'object' ? job : {}),
  })) as OptimizeJob[];
}

export function timeAgoShort(unixSec: number): string {
  const secs = Math.floor(Date.now() / 1000 - unixSec);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}min`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}j`;
}

export function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-dim">{label}</div>
      <div className="font-mono font-semibold">{value}</div>
    </div>
  );
}
