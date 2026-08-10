'use client';

/**
 * P0-3 — Conteneur de l'éditeur de `strategy_params`.
 *
 * `StrategyParamsEditor` est purement présentationnel : il reçoit une stratégie,
 * ses paramètres, et un `onSave`. Il lui manquait un hôte — le composant existait
 * mais aucune page ne le rendait, si bien que la route
 * `POST /api/config/strategy-params` et le hook `useSetStrategyParams` restaient
 * inatteignables depuis l'interface, exactement comme avant sa création.
 *
 * Ce panneau fournit ce qui manquait : la liste des stratégies configurées, la
 * sélection, et le branchement de la sauvegarde.
 *
 * Rappel de sémantique : ces paramètres sont le bloc `params:` par défaut des
 * `strategies/*.yaml`. Ils ne sont PAS l'`optimizer_results` écrit par
 * l'optimiseur — lequel a priorité à la résolution (cf.
 * `app/core/param_resolution.py`). Éditer ici n'écrase donc pas un paramétrage
 * optimisé, et c'est dit à l'utilisateur.
 */

import { useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from '@/components/ui/select';
import { SlidersHorizontal, Info } from 'lucide-react';
import { useConfig, useSetStrategyParams } from '@/hooks/use-api';
import { StrategyParamsEditor } from '@/components/cards/strategy-params-editor';

export function StrategyParamsPanel() {
  const { data: config, isLoading } = useConfig();
  const setParams = useSetStrategyParams();
  const [selected, setSelected] = useState<string>('');

  /** Stratégies qui déclarent effectivement un bloc `params:` éditable. */
  const strategies = useMemo(() => {
    const sp = (config as any)?.strategy_params ?? {};
    return Object.keys(sp)
      .filter((name) => sp[name] && typeof sp[name] === 'object' && Object.keys(sp[name]).length > 0)
      .sort();
  }, [config]);

  const current = selected || strategies[0] || '';
  const params = ((config as any)?.strategy_params ?? {})[current] ?? {};

  const handleSave = async (strategy: string, next: Record<string, any>) => {
    await setParams.mutateAsync({ strategy, params: next });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <SlidersHorizontal className="w-4 h-4 text-cyan-400" />
          Paramètres de stratégie
          <Badge variant="info" className="text-[10px]">P0-3</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="flex items-start gap-2 text-xs text-muted">
          <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          <span>
            Ces valeurs sont le bloc <code className="font-mono">params:</code> par défaut des
            fichiers <code className="font-mono">strategies/*.yaml</code>. Un paramétrage issu de
            l&apos;optimiseur (<code className="font-mono">optimizer_results</code>) reste
            prioritaire à la résolution : éditer ici ne l&apos;écrase pas.
          </span>
        </p>

        {isLoading ? (
          <p className="text-xs text-dim">Chargement de la configuration…</p>
        ) : strategies.length === 0 ? (
          <p className="text-xs text-dim">
            Aucune stratégie ne déclare de paramètres éditables dans la configuration courante.
          </p>
        ) : (
          <>
            <div className="max-w-xs">
              <Select value={current} onValueChange={setSelected}>
                <SelectTrigger className="font-mono" aria-label="Stratégie à éditer">
                  <SelectValue placeholder="Choisir une stratégie" />
                </SelectTrigger>
                <SelectContent>
                  {strategies.map((s) => (
                    <SelectItem key={s} value={s}>{s}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {current && (
              <StrategyParamsEditor
                key={current}
                strategyName={current}
                params={params}
                onSave={handleSave}
              />
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
