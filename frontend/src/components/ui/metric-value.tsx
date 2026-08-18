/**
 * U-02 — n'affiche pas une sentinelle ou un Sharpe sur 2 trades comme une mesure.
 */
'use client';

interface Props {
  value: number | null | undefined;
  nObs?: number;
  minObs?: number;
  format?: (v: number) => string;
  sentinel?: number;
}

export function MetricValue({
  value, nObs, minObs = 10, format, sentinel = 999,
}: Props) {
  if (value == null || Number.isNaN(value)) {
    return <span className="text-dim">—</span>;
  }
  if (nObs != null && nObs < minObs) {
    return (
      <span className="text-dim" title={`${nObs} observations — non significatif`}>
        n/a
      </span>
    );
  }
  if (Math.abs(value) >= sentinel) {
    return <span title="sentinelle — aucune perte / drawdown mesurable">∞</span>;
  }
  return <span>{format ? format(value) : String(value)}</span>;
}
