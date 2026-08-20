'use client';

/**
 * E3-F2-US4 — Modal plein écran pour un graphique.
 *
 * Reprend le « fullscreen chart modal » de l'ancienne `backtest.html`. La
 * fermeture par Échap et le piège de focus sont fournis par Radix Dialog (déjà
 * wrappé en S1) — inutile de recâbler des handlers globaux comme le faisait le
 * JS inline de la page Jinja2.
 *
 * `children` est une fonction : le graphique plein écran est monté dans le
 * Dialog (recharts mesure son conteneur au montage). Le bouton vit dans le
 * header du parent — on ne re-rend PAS la vignette ici (évite un 2e chart
 * et un bouton `absolute -top-7` mal placé).
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
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`Afficher « ${title} » en plein écran`}
        title="Plein écran"
        className="p-1.5 rounded-md text-dim hover:text-foreground hover:bg-card-hover focus:outline-none focus:ring-2 focus:ring-primary-400"
      >
        <Maximize2 className="w-3.5 h-3.5" />
      </button>

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
