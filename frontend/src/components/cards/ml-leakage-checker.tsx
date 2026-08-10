'use client';

/**
 * P1-1 — Vérificateur de fuite temporelle, avant backtest.
 *
 * `GET /api/ml/registry/overlaps` répond à une question précise : la fenêtre
 * qu'on s'apprête à backtester recouvre-t-elle la fenêtre d'entraînement du
 * modèle actif ? Si oui, le modèle a déjà vu les données sur lesquelles on
 * l'évaluerait, et le résultat serait flatteur sans valoir quoi que ce soit.
 *
 * Pourquoi ici et pas dans le panneau de backtest : la route s'interroge par
 * couple `(timeframe, recette)`, qui est exactement la clé du registre affiché
 * sur cette page. Le panneau de backtest, lui, ne connaît que des noms de
 * stratégie — et surtout il dispose déjà de la réponse EXACTE, calculée par le
 * serveur sur l'artefact réellement chargé (`ml_info.overlap_warning`). Utiliser
 * cette route là-bas aurait interrogé la version active du moment, qui peut
 * différer de celle que le backtest a résolue.
 *
 * Ici, l'intérêt est l'inverse : savoir AVANT de lancer, pour choisir sa
 * fenêtre.
 */

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ShieldAlert, ShieldCheck, Loader2, HelpCircle } from 'lucide-react';
import { useMLOverlaps } from '@/hooks/use-api';
import type { ModelRegistryEntry } from '@/types';

function OverlapRow({
  tf, recipe, start, end,
}: { tf: string; recipe: string; start: string; end: string }) {
  const { data, isLoading, isError } = useMLOverlaps(tf, recipe, start, end, Boolean(start && end));

  let statut;
  if (!start || !end) {
    statut = <span className="text-dim text-xs">Indiquez une fenêtre</span>;
  } else if (isLoading) {
    statut = <Loader2 className="w-3.5 h-3.5 animate-spin text-muted" />;
  } else if (isError) {
    statut = <span className="text-xs text-muted">Indisponible</span>;
  } else if (!data?.active_version) {
    statut = (
      <span className="flex items-center gap-1.5 text-xs text-dim">
        <HelpCircle className="w-3.5 h-3.5" />
        Aucun modèle actif
      </span>
    );
  } else if (data.verdict === 'leak') {
    statut = (
      <span className="flex items-center gap-1.5 text-xs text-rose-400 font-semibold"
            title={data.reason}>
        <ShieldAlert className="w-3.5 h-3.5" />
        Fuite temporelle
      </span>
    );
  } else if (data.verdict === 'posterior') {
    // Cas le plus trompeur : `overlaps()` ne teste qu'une intersection, donc un
    // modèle entraîné APRÈS la fenêtre en sort « sans chevauchement ». Il
    // n'existait pourtant pas à la date testée.
    statut = (
      <span className="flex items-center gap-1.5 text-xs text-amber-400 font-semibold"
            title={data.reason}>
        <ShieldAlert className="w-3.5 h-3.5" />
        Postérieur à la fenêtre
      </span>
    );
  } else if (data.verdict === 'unknown') {
    statut = (
      <span className="flex items-center gap-1.5 text-xs text-dim" title={data.reason}>
        <HelpCircle className="w-3.5 h-3.5" />
        Non vérifiable
      </span>
    );
  } else {
    statut = (
      <span className="flex items-center gap-1.5 text-xs text-emerald-400"
            title={data.reason}>
        <ShieldCheck className="w-3.5 h-3.5" />
        Causalement valide
      </span>
    );
  }

  return (
    <tr className="border-b border-border/50">
      <td className="py-1.5 px-2 font-mono text-xs">{recipe}</td>
      <td className="py-1.5 px-2 font-mono text-xs text-muted">{tf}</td>
      <td className="py-1.5 px-2 font-mono text-[11px] text-dim">
        {data?.train_end ?? '—'}
      </td>
      <td className="py-1.5 px-2">{statut}</td>
    </tr>
  );
}

export function MLLeakageChecker({ entries }: { entries: ModelRegistryEntry[] }) {
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');

  if (!entries || entries.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <ShieldAlert className="w-4 h-4 text-cyan-400" />
          Fuite temporelle — vérification avant backtest
          <Badge variant="info" className="text-[10px]">P1-1</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted">
          Un modèle figé évalué sur une période qu&apos;il a vue à l&apos;entraînement produit des
          performances qui ne se reproduiront jamais en live. Indiquez la fenêtre que vous
          comptez backtester : chaque recette dit si son modèle actif la recouvre.
        </p>

        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label htmlFor="leak-start" className="text-xs">Début de fenêtre</Label>
            <Input
              id="leak-start" type="date" value={start}
              onChange={(e) => setStart(e.target.value)} className="w-40"
            />
          </div>
          <div>
            <Label htmlFor="leak-end" className="text-xs">Fin de fenêtre</Label>
            <Input
              id="leak-end" type="date" value={end}
              onChange={(e) => setEnd(e.target.value)} className="w-40"
            />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-muted">
                <th scope="col" className="py-1 px-2 text-left">Recette</th>
                <th scope="col" className="py-1 px-2 text-left">TF</th>
                <th scope="col" className="py-1 px-2 text-left">Fin d&apos;entraînement</th>
                <th scope="col" className="py-1 px-2 text-left">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <OverlapRow
                  key={`${e.recipe}@${e.tf}`}
                  tf={e.tf} recipe={e.recipe} start={start} end={end}
                />
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
