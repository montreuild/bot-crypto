'use client';

/**
 * E3-F2-US4 — Modal plein écran pour un graphique.
 *
 * Reprend le « fullscreen chart modal » de l'ancienne `backtest.html`. La
 * fermeture par Échap et le piège de focus sont fournis par Radix Dialog (déjà
 * wrappé en S1) — inutile de recâbler des handlers globaux comme le faisait le
 * JS inline de la page Jinja2.
 *
 * `children` est une fonction et non un noeud : le graphique est monté DEUX
 * fois (vignette + plein écran) avec des hauteurs différentes, et recharts
 * mesure son conteneur au montage. Passer le même élément aux deux endroits
 * donnerait un graphique plein écran figé à la taille de la vignette.
 */

import { useState, type ReactNode } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Maximize2 } from 'lucide-react';

export function ChartFullscreen({
  title,
  children,
}: {
  title: string;
  children: (opts: { fullscreen: boolean }) => ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label={`Afficher « ${title} » en plein écran`}
          title="Plein écran"
          className="absolute right-0 -top-7 z-10 p-1.5 rounded-md text-dim hover:text-foreground hover:bg-card-hover focus:outline-none focus:ring-2 focus:ring-primary-400"
        >
          <Maximize2 className="w-3.5 h-3.5" />
        </button>
        {children({ fullscreen: false })}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-[95vw] w-[95vw] h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
          </DialogHeader>
          <div className="flex-1 min-h-0">{open && children({ fullscreen: true })}</div>
        </DialogContent>
      </Dialog>
    </>
  );
}
