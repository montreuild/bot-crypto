/**
 * S1-F3-US8 — Page d'erreur 500 personnalisée (App Router error boundary).
 *
 * Catch toutes les erreurs non gérées dans les Server Components et Client
 * Components. Affiche un message clair + bouton reset.
 */

'use client';

import { AlertTriangle, RefreshCw } from 'lucide-react';
import { useEffect } from 'react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log l'erreur pour monitoring (à brancher sur Sentry/PostHog plus tard)
    console.error('[App Error]', error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center min-h-[80vh] px-4 text-center">
      <div className="w-14 h-14 rounded-full bg-red-500/10 border border-red-500/30 flex items-center justify-center mb-4">
        <AlertTriangle className="w-7 h-7 text-red-400" />
      </div>
      <h1 className="text-xl font-semibold mb-2">Une erreur est survenue</h1>
      <p className="text-muted mb-2 max-w-md">
        Le composant a rencontré une erreur inattendue. Essayez de réessayer,
        ou rechargez la page si le problème persiste.
      </p>
      {error.digest && (
        <p className="text-xs text-dim font-mono mb-6">
          ID erreur : {error.digest}
        </p>
      )}
      <button
        onClick={reset}
        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold bg-primary-500/10 text-primary-400 border border-primary-500/30 hover:bg-primary-500/20 transition-colors"
      >
        <RefreshCw className="w-4 h-4" />
        Réessayer
      </button>
    </div>
  );
}
