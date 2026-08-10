'use client';

/** P2-4 : Utilitaires extraits de optimizer-view.tsx. */

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
