'use client';

/**
 * Sélecteur de stratégies unique (Laboratoire) — même rendu que l'Optimizer :
 * chips, badge ML, TF recommandés, Toutes / Aucune. Aucune sélection par défaut.
 */

import { type ReactNode, useId } from 'react';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { OptimizeSpaces } from '@/types';

/**
 * Nom accessible complet de la puce. Les badges vivent DANS le bouton : leur
 * `title` était inatteignable au clavier et au tactile, et un tooltip Radix y
 * imbriquerait un second élément focusable. L'information remonte donc dans le
 * nom du bouton, et les badges deviennent décoratifs.
 */
function libelleChip(
  nom: string, actif: boolean, isMl: boolean, tfs: string[], alerte: boolean,
): string {
  const parts = [nom];
  if (isMl) parts.push('stratégie ML');
  if (tfs.length > 0) parts.push(`timeframes recommandés : ${tfs.join(', ')}`);
  if (alerte) parts.push('attention, un timeframe sélectionné n’est pas recommandé');
  parts.push(actif ? 'sélectionnée' : 'non sélectionnée');
  return parts.join(' — ');
}

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
  const idGroupe = useId();
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
        <span id={`${idGroupe}-titre`} className="text-xs text-dim">
          Stratégies ({value.length}/{strategies.length})
        </span>
        <button
          type="button"
          onClick={() => onChange([...strategies])}
          disabled={strategies.length === 0}
          className="px-2 py-0.5 rounded text-[0.6875rem] border border-border text-muted hover:text-foreground hover:border-border-hi disabled:opacity-40"
        >
          Toutes
        </button>
        <button
          type="button"
          onClick={() => onChange([])}
          className="px-2 py-0.5 rounded text-[0.6875rem] border border-border text-muted hover:text-foreground hover:border-border-hi"
        >
          Aucune
        </button>
        {extra}
      </div>
      {strategies.length === 0 ? (
        <p className="text-xs text-dim">Chargement des stratégies…</p>
      ) : (
        <div
          role="group"
          aria-labelledby={`${idGroupe}-titre`}
          className="flex flex-wrap gap-2"
        >
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
                aria-pressed={active}
                aria-label={libelleChip(s, active, isMl, tfs, hasWarn)}
                title={libelleChip(s, active, isMl, tfs, hasWarn)}
                className={cn(
                  'px-3 py-1.5 rounded-lg text-xs font-mono border transition-all inline-flex items-center gap-1',
                  active
                    ? 'bg-primary-500/15 text-primary-400 border-primary-500/40'
                    : 'bg-card-hover text-muted border-border hover:border-border-hi',
                )}
              >
                {/* L'état ne doit pas reposer sur la seule couleur (WCAG 1.4.1). */}
                <Check
                  aria-hidden="true"
                  className={cn('w-3 h-3 shrink-0', active ? 'opacity-100' : 'opacity-0')}
                />
                {s}
                {isMl && <span aria-hidden="true" className="text-purple-400">ML</span>}
                {tfs.length > 0 && hasWarn && (
                  <span aria-hidden="true" className="inline-flex gap-0.5">
                    {tfs.slice(0, 3).map((tf) => (
                      <span
                        key={tf}
                        className="px-1 rounded text-[0.625rem] bg-cyan-500/15 text-cyan-300 border border-cyan-500/30"
                      >
                        {tf}
                      </span>
                    ))}
                    <span className="px-1 rounded text-[0.625rem] bg-amber-500/15 text-amber-300 border border-amber-500/30">
                      ⚠
                    </span>
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
