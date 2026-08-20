'use client';

/**
 * Sélecteur de stratégies partagé (Laboratoire).
 * Chips cliquables style Optimizer + raccourcis Toutes / Aucune.
 * Défaut : aucune sélection (l'appelant initialise à []).
 */

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function StrategyPicker({
  strategies,
  value,
  onChange,
  enabled,
  isMl,
  label = 'Stratégies',
  extra,
  trailing,
}: {
  strategies: string[];
  value: string[];
  onChange: (next: string[]) => void;
  enabled?: string[];
  isMl?: (name: string) => boolean;
  label?: string;
  extra?: ReactNode;
  trailing?: (name: string, active: boolean) => ReactNode;
}) {
  const selected = new Set(value);
  const toggle = (s: string) => {
    onChange(selected.has(s) ? value.filter((x) => x !== s) : [...value, s]);
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <span className="text-xs text-dim">
          {label} ({value.length}/{strategies.length})
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
            const live = enabled?.includes(s);
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
                {isMl?.(s) && <span className="text-purple-400">ML</span>}
                {live && (
                  <span className="text-[0.55rem] text-emerald-400">●</span>
                )}
                {trailing?.(s, active)}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
