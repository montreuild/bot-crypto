'use client';

/**
 * P0-4 — Badge overfitting_gate réutilisable.
 *
 * Affiche le niveau de l'overfitting gate (block/warn/good/strong) calculé
 * par `app/ml/overfitting_gate.validate_model_quality` et exposé via
 * `ArtifactRef.to_dict().overfitting_gate` (route /api/ml/registry et
 * /api/ml/registry/versions).
 *
 * Niveaux :
 *   - block  (rouge)  : AUC < 0.55 — edge trop faible, biais de sélection probable
 *   - warn   (ambre)  : 0.55 ≤ AUC < 0.60 — edge acceptable mais à surveiller
 *   - good   (vert)   : 0.60 ≤ AUC < 0.70 — edge bon
 *   - strong (vert)   : AUC ≥ 0.70 — edge fort (vérifier pas d'overfitting)
 *
 * Si `overfittingGate` est null/undefined, n'affiche rien (comportement
 * rétro-compatible — les anciens modèles sans le champ ne sont pas pénalisés).
 */

import { Badge } from '@/components/ui/badge';
import { ShieldCheck, AlertTriangle, AlertOctagon, TrendingUp } from 'lucide-react';

export interface OverfittingGateData {
  ok?: boolean;
  auc?: number;
  level?: string;  // 'block' | 'warn' | 'good' | 'strong'
  reason?: string;
  min_significant_samples?: number;
}

interface Props {
  /** Données overfitting_gate depuis ArtifactRef.to_dict().overfitting_gate */
  overfittingGate?: OverfittingGateData | null;
  /** Taille du badge (défaut 'sm'). */
  size?: 'sm' | 'md';
  /** Afficher l'AUC à côté du badge (défaut true). */
  showAuc?: boolean;
  className?: string;
}

const LEVEL_CONFIG: Record<string, {
  variant: 'danger' | 'warning' | 'success' | 'info';
  icon: typeof AlertOctagon;
  label: string;
}> = {
  block: { variant: 'danger', icon: AlertOctagon, label: 'Block' },
  warn: { variant: 'warning', icon: AlertTriangle, label: 'Warn' },
  good: { variant: 'success', icon: ShieldCheck, label: 'Good' },
  strong: { variant: 'success', icon: TrendingUp, label: 'Strong' },
};

export function OverfittingGateBadge({
  overfittingGate,
  size = 'sm',
  showAuc = true,
  className,
}: Props) {
  if (!overfittingGate || !overfittingGate.level) {
    return null;
  }

  const cfg = LEVEL_CONFIG[overfittingGate.level] ?? LEVEL_CONFIG.warn;
  const Icon = cfg.icon;
  const auc = overfittingGate.auc;
  const textSize = size === 'sm' ? 'text-[10px]' : 'text-xs';

  return (
    <span className={`inline-flex items-center gap-1 ${className ?? ''}`} title={overfittingGate.reason ?? ''}>
      <Badge variant={cfg.variant} className={textSize}>
        <Icon className={size === 'sm' ? 'w-2.5 h-2.5' : 'w-3 h-3'} />
        {cfg.label}
      </Badge>
      {showAuc && auc != null && Number.isFinite(auc) && (
        <span className={`font-mono ${textSize} text-muted`}>
          AUC {Number(auc).toFixed(3)}
        </span>
      )}
    </span>
  );
}
