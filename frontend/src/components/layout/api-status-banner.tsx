'use client';

/**
 * S6-12 — Bandeau global « backend injoignable ».
 *
 * Le sondage `useBotStatus()` tourne déjà toutes les 3 s sur chaque page (il
 * alimente la Topbar). On réutilise son erreur : dès que le backend ne répond
 * plus, l'utilisateur voit *pourquoi* toutes les pages sont vides, sur toutes
 * les pages à la fois, sans avoir à ouvrir la console.
 *
 * Se retire tout seul dès que l'API répond de nouveau.
 */

import { PlugZap, X } from 'lucide-react';
import { useState } from 'react';
import { useBotStatus } from '@/hooks/use-api';
import { isBackendUnreachable } from '@/lib/api';

export function ApiStatusBanner() {
  const { error } = useBotStatus();
  const [dismissed, setDismissed] = useState(false);

  const down = isBackendUnreachable(error);

  // Ajustement d'état pendant le render (motif React « adjusting state when a
  // prop changes ») : au retour du backend on réarme le bandeau, pour qu'un
  // masquage manuel ne vaille que pour la coupure en cours.
  const [wasDown, setWasDown] = useState(down);
  if (down !== wasDown) {
    setWasDown(down);
    if (!down) setDismissed(false);
  }

  if (!down || dismissed) return null;

  return (
    <div
      role="alert"
      className="flex items-center gap-3 px-4 py-2.5 bg-amber-500/10 border-b border-amber-500/30 text-amber-200 text-sm flex-shrink-0"
    >
      <PlugZap className="w-4 h-4 flex-shrink-0 text-amber-400" />
      <p className="flex-1 min-w-0">
        <span className="font-semibold">Backend injoignable</span>
        {' — '}
        aucune donnée ne peut être chargée. Démarrez l&apos;API avec{' '}
        <code className="font-mono text-xs px-1.5 py-0.5 rounded bg-background/60 text-foreground">
          python cli.py --web
        </code>
        , puis réessayez.
      </p>
      <button
        onClick={() => setDismissed(true)}
        aria-label="Masquer l'avertissement"
        className="p-1 rounded hover:bg-amber-500/20 transition-colors flex-shrink-0"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
