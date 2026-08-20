'use client';

/**
 * Sélecteur de stratégies unique (Laboratoire) — même rendu que l'Optimizer :
 * chips, badge ML, TF recommandés, Toutes / Aucune. Aucune sélection par défaut.
 */

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import type { OptimizeSpaces } from '@/types';

export function StrategyPicker({
  strategies,
  value,
  onChange,
  spaces,
  selectedTfs,
  extra,
}: {
  strategies: string[];
  value: string[];
  onChange: (next: string[]) => void;
  spaces?: OptimizeSpaces;
  selectedTfs?: string[];
  extra?: ReactNode;
}) {
  const selected = new Set(value);
  const toggle = (s: string) => {
    onChange(selected.has(s) ? value.filter((x) => x !== s) : [...value, s]);
  };

  const recTfs = (s: string): string[] => {
    const info = spaces?.[s];
    if (!info) return [];
    return info.recommended_tfs ?? info.timeframes ?? [];
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <span className="text-xs text-dim">
          Stratégies ({value.length}/{strategies.length})
        </span>
        <button
          type="button"
          onClick={() => onChange([...strategies])}
          disabled={strategies.length === 0}
          className="px-2 py-0.5 rounded text-[10px] border border-border text-muted hover:text-foreground hover:border-border-hi disabled:opacity-40"
        >
          Toutes
        </button>
        <button
          type="button"
          onClick={() => onChange([])}
          className="px-2 py-0.5 rounded text-[10px] border border-border text-muted hover:text-foreground hover:border-border-hi"
        >
          Aucune
        </button>
        {extra}
      </div>
      {strategies.length === 0 ? (
        <p className="text-xs text-dim">Chargement des stratégies…</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {strategies.map((s) => {
            const active = selected.has(s);
            const isMl = !!spaces?.[s]?.is_ml;
            const tfs = recTfs(s);
            const hasWarn = active && (selectedTfs?.length ?? 0) > 0 && tfs.length > 0
              && selectedTfs!.some((tf) => !tfs.includes(tf));
            return (
              <button
                key={s}
                type="button"
                onClick={() => toggle(s)}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-xs font-mono border transition-all inline-flex items-center gap-1',
                  active
                    ? 'bg-primary-500/15 text-primary-400 border-primary-500/40'
                    : 'bg-card-hover text-muted border-border hover:border-border-hi',
                )}
              >
                {s}
                {isMl && <span className="text-purple-400">ML</span>}
                {tfs.length > 0 && (
                  <span className="inline-flex gap-0.5">
                    {tfs.slice(0, 3).map((tf) => (
                      <span
                        key={tf}
                        className="px-1 rounded text-[0.55rem] bg-cyan-500/15 text-cyan-300 border border-cyan-500/30"
                        title={`TF recommandé : ${tf}`}
                      >
                        {tf}
                      </span>
                    ))}
                    {hasWarn && (
                      <span
                        className="px-1 rounded text-[0.55rem] bg-amber-500/15 text-amber-300 border border-amber-500/30"
                        title="Au moins un TF sélectionné n'est pas recommandé"
                      >
                        ⚠
                      </span>
                    )}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
