'use client';

/**
 * États de page normalisés pour les vues alimentées par TanStack Query.
 *
 * S6-12 — Avant, chaque page gardait ses données derrière `if (isLoading ||
 * !data) return <spinner/>`. Sur erreur réseau, react-query repasse
 * `isLoading` à `false` mais laisse `data` à `undefined` : la condition reste
 * vraie et la page tourne indéfiniment, sans titre ni message. Les pages
 * Jinja2, rendues côté serveur, n'avaient pas ce mode de défaillance.
 *
 * `<QueryBoundary>` remplace ce motif : le titre de page reste toujours
 * monté (accessibilité — une page a toujours un `h1`), et l'échec devient
 * visible et réessayable.
 */

import { AlertTriangle, Loader2, PlugZap, RefreshCw } from 'lucide-react';
import { ReactNode, useEffect, useState } from 'react';
import { isBackendUnreachable } from '@/lib/api';
import { cn } from '@/lib/utils';

/** Sous-ensemble de `UseQueryResult` dont dépendent les états de page. */
export type QueryLike = {
  data?: unknown;
  error?: unknown;
  failureReason?: unknown;
  isFetching?: boolean;
};

/**
 * Retient la dernière erreur jusqu'au prochain succès.
 *
 * Nécessaire parce que nos queries ont un `refetchInterval` court (3 s pour le
 * statut). Une nouvelle tentative repart avant que la séquence de retry ne se
 * stabilise : react-query repasse en `pending` et remet `error`/`failureReason`
 * à `null` le temps du vol. Une vue qui lit `error` directement alterne donc
 * erreur → spinner → erreur à chaque tick, ce qui donne un clignotement — et,
 * quand les tentatives s'enchaînent assez vite, l'erreur n'apparaît jamais.
 *
 * On mémorise l'échec et on ne l'efface qu'à l'arrivée de données réelles.
 */
export function useStickyError(query: QueryLike): unknown {
  const live = query.error ?? query.failureReason ?? null;
  const hasData = query.data !== undefined;
  const [sticky, setSticky] = useState<unknown>(live);

  useEffect(() => {
    if (hasData) setSticky(null);
    else if (live) setSticky(live);
  }, [live, hasData]);

  return hasData ? null : (sticky ?? live);
}

export function LoadingState({ label = 'Chargement…', className }: { label?: string; className?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn('flex items-center justify-center min-h-[40vh]', className)}
    >
      <div className="text-center">
        <Loader2 className="w-8 h-8 mx-auto mb-3 text-primary-400 animate-spin" />
        <div className="text-sm text-muted">{label}</div>
      </div>
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  isRetrying,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  isRetrying?: boolean;
  className?: string;
}) {
  const unreachable = isBackendUnreachable(error);
  const message = error instanceof Error ? error.message : String(error ?? 'Erreur inconnue');

  return (
    <div
      role="alert"
      className={cn('flex items-center justify-center min-h-[40vh]', className)}
    >
      <div className="max-w-lg w-full rounded-xl border border-border bg-card p-6 text-center">
        <div
          className={cn(
            'w-11 h-11 mx-auto mb-4 rounded-full flex items-center justify-center border',
            unreachable
              ? 'bg-amber-500/10 text-amber-400 border-amber-500/30'
              : 'bg-red-500/10 text-red-400 border-red-500/30',
          )}
        >
          {unreachable ? <PlugZap className="w-5 h-5" /> : <AlertTriangle className="w-5 h-5" />}
        </div>

        <h2 className="text-base font-semibold mb-2">
          {unreachable ? 'Backend injoignable' : 'Impossible de charger les données'}
        </h2>

        <p className="text-sm text-muted mb-4 break-words">{message}</p>

        {unreachable && (
          <div className="text-left text-xs text-muted bg-background rounded-lg border border-border p-3 mb-4">
            <div className="mb-2 text-dim uppercase tracking-wider text-[10px] font-semibold">
              Démarrer le backend
            </div>
            <code className="block font-mono text-[11px] text-foreground whitespace-pre-wrap">
              python cli.py --web
            </code>
            <p className="mt-2 leading-relaxed">
              L&apos;API est attendue sur{' '}
              <span className="font-mono text-foreground">
                {process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}
              </span>{' '}
              — ajustable via <span className="font-mono">NEXT_PUBLIC_API_URL</span>.
            </p>
          </div>
        )}

        {onRetry && (
          <button
            onClick={onRetry}
            disabled={isRetrying}
            className={cn(
              'inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all',
              'bg-primary-500/10 text-primary-400 border border-primary-500/30 hover:bg-primary-500/20',
              isRetrying && 'opacity-50 cursor-not-allowed',
            )}
          >
            <RefreshCw className={cn('w-4 h-4', isRetrying && 'animate-spin')} />
            Réessayer
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ label = 'Aucune donnée disponible', className }: { label?: string; className?: string }) {
  return (
    <div className={cn('flex items-center justify-center min-h-[40vh]', className)}>
      <div className="text-sm text-muted">{label}</div>
    </div>
  );
}

/**
 * Enveloppe une vue de page : affiche le chargement, l'erreur (réessayable) ou
 * le contenu. `title` reste monté dans les trois cas — c'est ce qui garantit
 * qu'une page a toujours un `h1`, y compris backend éteint.
 */
export function QueryBoundary({
  query,
  isEmpty,
  onRetry,
  loadingLabel,
  title,
  children,
}: {
  query: QueryLike;
  isEmpty?: boolean;
  onRetry?: () => void;
  loadingLabel?: string;
  title?: ReactNode;
  children: ReactNode;
}) {
  // L'erreur persiste jusqu'au prochain succès : sans ça, le `refetchInterval`
  // ferait alterner erreur et spinner à chaque tentative (cf. useStickyError).
  const error = useStickyError(query);
  const hasData = query.data !== undefined;

  let body: ReactNode;
  if (error) body = <ErrorState error={error} onRetry={onRetry} isRetrying={query.isFetching} />;
  else if (!hasData) body = <LoadingState label={loadingLabel} />;
  else if (isEmpty) body = <EmptyState />;
  else body = children;

  return (
    <div className="space-y-6 animate-fade-in">
      {title}
      {body}
    </div>
  );
}
