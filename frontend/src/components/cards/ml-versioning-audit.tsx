'use client';

/**
 * P0-5 — Card « Audit versioning des modèles ML ».
 *
 * Affiche le résultat de `/api/ml/versioning/audit` (migration_check) qui
 * scanne le répertoire `models/` et retourne :
 *   - total : nombre total de modèles
 *   - with_hash : modèles avec hash de features (versioning actif)
 *   - without_hash : modèles sans hash (à re-entraîner)
 *   - incompatible : modèles dont le hash ne correspond plus aux features
 *     actuelles (features drift)
 *
 * Permet à l'utilisateur de détecter les modèles obsolètes et de lancer
 * un re-entraînement pour activer le versioning.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { useMLVersioningAudit } from '@/hooks/use-api';
import { Loader2, ShieldCheck, AlertTriangle, XCircle, Database } from 'lucide-react';
import { cn } from '@/lib/utils';

export function MLVersioningAudit({ className }: { className?: string }) {
  const { data, isLoading, isError } = useMLVersioningAudit();

  if (isLoading) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Database className="w-4 h-4" />
            Audit versioning
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="p-4 text-center text-muted text-sm">
            <Loader2 className="w-4 h-4 animate-spin inline mr-2" />
            Scan du registre...
          </div>
        </CardContent>
      </Card>
    );
  }

  if (isError || !data) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Database className="w-4 h-4" />
            Audit versioning
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="p-4 text-center text-red-400 text-sm">
            Audit indisponible
          </div>
        </CardContent>
      </Card>
    );
  }

  const summary = data.summary;
  const coverage = summary.coverage_pct;
  const hasIssues = summary.without_hash > 0 || summary.incompatible > 0;

  return (
    <Card className={cn(className, hasIssues && 'border-l-4 border-l-amber-500')}>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Database className="w-4 h-4" />
            Audit versioning
          </span>
          {hasIssues ? (
            <Badge variant="warning" className="text-[10px]">
              <AlertTriangle className="w-2.5 h-2.5" />
              Action requise
            </Badge>
          ) : (
            <Badge variant="success" className="text-[10px]">
              <ShieldCheck className="w-2.5 h-2.5" />
              Conforme
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Coverage % */}
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted">Coverage versioning</span>
          <span className={cn(
            'font-mono font-semibold text-sm',
            coverage >= 90 ? 'text-emerald-400' : coverage >= 50 ? 'text-amber-400' : 'text-red-400',
          )}>
            {coverage.toFixed(1)}%
          </span>
        </div>

        {/* Barre de progression coverage */}
        <div className="h-2 bg-card-hover/50 rounded-full overflow-hidden">
          <div
            className={cn(
              'h-full transition-all',
              coverage >= 90 ? 'bg-emerald-500' : coverage >= 50 ? 'bg-amber-500' : 'bg-red-500',
            )}
            style={{ width: `${Math.min(100, coverage)}%` }}
          />
        </div>

        {/* Stats détaillées */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          <div className="p-2 rounded-md bg-card-hover/30 flex items-center gap-2">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" />
            <div>
              <div className="text-[10px] text-dim uppercase">Avec hash</div>
              <div className="font-mono font-semibold">{summary.with_hash}/{summary.total}</div>
            </div>
          </div>
          <div className="p-2 rounded-md bg-card-hover/30 flex items-center gap-2">
            <AlertTriangle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
            <div>
              <div className="text-[10px] text-dim uppercase">Sans hash</div>
              <div className="font-mono font-semibold text-amber-400">{summary.without_hash}</div>
            </div>
          </div>
          {summary.incompatible > 0 && (
            <div className="p-2 rounded-md bg-red-500/10 border border-red-500/30 flex items-center gap-2 col-span-2">
              <XCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
              <div>
                <div className="text-[10px] text-dim uppercase">Incompatibles</div>
                <div className="font-mono font-semibold text-red-400">
                  {summary.incompatible} modèle(s) — features drift détecté
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Avertissement + CTA */}
        {hasIssues && (
          <div className="p-2 rounded-md bg-amber-500/10 border border-amber-500/30 text-[11px] text-amber-300">
            <strong>⚠ {summary.without_hash} modèle(s) sans hash de features.</strong>{' '}
            Re-entraînez ces modèles pour activer le versioning et détecter
            les drifts futurs. Les modèles incompatibles ({summary.incompatible})
            doivent être re-entraînés obligatoirement.
            <div className="mt-1.5">
              <a
                href="/lab?tab=ml"
                className="text-amber-400 hover:text-amber-300 underline"
              >
                Lancer un re-entraînement →
              </a>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
