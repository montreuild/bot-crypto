'use client';

/**
 * S2-F2-US3 — Frise de cycle de vie d'un bot.
 *
 * Visualisation linéaire : Candidat → Essai → Actif → Retiré
 * 4 étapes avec dots colorés et dates de transition.
 */

import { cn } from '@/lib/utils';

interface LifecycleFriezeProps {
  currentState: string;
  transitions?: Array<{ state: string; date?: string }>;
  className?: string;
}

const STEPS = [
  { key: 'candidat', label: 'Candidat', color: 'amber' },
  { key: 'essai', label: 'Essai', color: 'cyan' },
  { key: 'actif', label: 'Actif', color: 'emerald' },
  { key: 'retire', label: 'Retiré', color: 'red' },
];

const colorClasses: Record<string, { dot: string; line: string; text: string }> = {
  amber: { dot: 'bg-amber-400', line: 'bg-amber-400/50', text: 'text-amber-400' },
  cyan: { dot: 'bg-cyan-400', line: 'bg-cyan-400/50', text: 'text-cyan-400' },
  emerald: { dot: 'bg-emerald-400', line: 'bg-emerald-400/50', text: 'text-emerald-400' },
  red: { dot: 'bg-red-400', line: 'bg-red-400/50', text: 'text-red-400' },
  muted: { dot: 'bg-slate-600', line: 'bg-slate-700', text: 'text-muted' },
};

export function LifecycleFrieze({ currentState, transitions = [], className }: LifecycleFriezeProps) {
  const currentIdx = STEPS.findIndex((s) => s.key === currentState);
  if (currentIdx === -1) return null;

  // Map transitions by state for date lookup
  const transitionMap = new Map<string, string>();
  transitions.forEach((t) => {
    if (t.date) transitionMap.set(t.state, t.date);
  });

  return (
    <div className={cn('w-full', className)}>
      <div className="text-xs text-dim uppercase tracking-wider font-semibold mb-3">
        Cycle de vie
      </div>
      <div className="flex items-center">
        {STEPS.map((step, idx) => {
          const isDone = idx < currentIdx;
          const isCurrent = idx === currentIdx;
          const isFuture = idx > currentIdx;
          const colorKey = isDone ? step.color : isCurrent ? step.color : 'muted';
          const colors = colorClasses[colorKey];
          const date = transitionMap.get(step.key);

          return (
            <div key={step.key} className="flex items-center flex-1 last:flex-none">
              {/* Dot + label */}
              <div className="flex flex-col items-center gap-1.5 min-w-[60px]">
                <div className="relative">
                  <div
                    className={cn(
                      'w-3.5 h-3.5 rounded-full border-2 border-card',
                      colors.dot,
                      isCurrent && 'ring-4 ring-offset-0 ring-offset-card',
                      isCurrent && colors.dot.replace('bg-', 'ring-').replace('/50', '/30'),
                    )}
                  />
                  {isCurrent && (
                    <div className={cn('absolute inset-0 rounded-full animate-ping opacity-30', colors.dot)} />
                  )}
                </div>
                <div className={cn(
                  'text-[10px] font-medium uppercase tracking-wider',
                  isFuture ? 'text-dim' : colors.text,
                )}>
                  {step.label}
                </div>
                {date && (
                  <div className="text-[9px] text-dim font-mono">
                    {new Date(date).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })}
                  </div>
                )}
              </div>
              {/* Line to next step */}
              {idx < STEPS.length - 1 && (
                <div className={cn(
                  'h-0.5 flex-1 mx-2 transition-colors',
                  idx < currentIdx ? colors.line : 'bg-border',
                )} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
