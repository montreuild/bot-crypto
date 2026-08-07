'use client';

/**
 * RPL-009 — Log panel horodaté du chargement du replay.
 *
 * Extrait du template Jinja2 `replay.html:90-149`.
 * Format d'une ligne : `HH:MM:SS · <niveau> · <message>`.
 *
 * L'autre moitié de RPL-009 (welcome screen, 4 tips cards) vit dans
 * `views/replay-view.tsx`.
 */

import { useEffect, useRef } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export type ReplayLogLevel = 'info' | 'ok' | 'warn' | 'error';

export interface ReplayLogEntry {
  /** Epoch ms — formaté à l'affichage, pas au push (évite les surprises de fuseau). */
  at: number;
  level: ReplayLogLevel;
  message: string;
}

const LEVEL_STYLE: Record<ReplayLogLevel, { label: string; className: string }> = {
  info:  { label: 'info', className: 'text-cyan-400' },
  ok:    { label: 'ok',   className: 'text-emerald-400' },
  warn:  { label: 'warn', className: 'text-amber-400' },
  error: { label: 'err',  className: 'text-rose-400' },
};

function hhmmss(at: number): string {
  return new Date(at).toLocaleTimeString('fr-FR', { hour12: false });
}

interface Props {
  entries: ReplayLogEntry[];
  /** Titre optionnel — permet de réutiliser le panneau ailleurs. */
  title?: string;
}

export function ReplayLoadLog({ entries, title = 'Journal de chargement' }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll vers la dernière ligne : pendant un fetch long, c'est la ligne
  // la plus récente qui intéresse, pas la première.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries]);

  if (entries.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div
          ref={scrollRef}
          role="log"
          aria-live="polite"
          aria-label={title}
          className="max-h-48 overflow-y-auto font-mono text-xs space-y-1 pr-1"
        >
          {entries.map((e, i) => {
            const style = LEVEL_STYLE[e.level] ?? LEVEL_STYLE.info;
            return (
              <div key={`${e.at}-${i}`} className="flex gap-2">
                <span className="text-muted-foreground shrink-0">{hhmmss(e.at)}</span>
                <span className="text-dim shrink-0" aria-hidden>·</span>
                <span className={`${style.className} shrink-0 w-8`}>{style.label}</span>
                <span className="text-dim shrink-0" aria-hidden>·</span>
                <span className="text-foreground break-all">{e.message}</span>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
