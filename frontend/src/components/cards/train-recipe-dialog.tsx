'use client';

/**
 * ML-003 — Dialogue d'entraînement direct d'une recette ML.
 *
 * Remplace le renvoi vers `/models?recipe=…` depuis `MLRecipesList` :
 * l'utilisateur expert peut lancer l'entraînement sans quitter l'onglet ML.
 *
 * Pipeline :
 * 1. `api.startMLTrain({ strategy: recipe, symbol, tf, window_bars })` → renvoie `job_id`.
 *    (L'API backend attend `strategy` ; le `recipe` est passé tel quel car
 *    les recettes sont des stratégies ML.)
 * 2. Polling `api.getMLTrainStatus(jobId)` toutes les ~1,5 s via
 *    `useMLTrainStatus` (le hook existe déjà ; le SSE n'existe pas côté
 *    backend, cf. audit ML). Le polling s'arrête automatiquement quand
 *    `status !== 'running'`.
 * 3. On success : toast « Modèle entraîné » + invalidation des queries
 *    `mlInfo` (StrategyTable du tab ML) et `ml-recipes`.
 * 4. Un lien secondaire « Voir dans le registre » reste disponible pour
 *    basculer vers la page `/models` (gate de promotion, pin, sweep) qui
 *    n'est pas dans le plan de fusion du Lab.
 */

import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { api } from '@/lib/api';
import { useMLTrainStatus } from '@/hooks/use-api';
import { toast } from 'sonner';
import {
  Loader2, Rocket, CheckCircle2, XCircle, ExternalLink, AlertCircle,
} from 'lucide-react';
import Link from 'next/link';
import { cn } from '@/lib/utils';

export interface TrainRecipeDialogRecipe {
  recipe: string;
  trainable?: boolean;
  reason?: string | null;
  features_catalog?: string | null;
  label_scheme?: string | null;
  heads?: string[];
}

interface TrainRecipeDialogProps {
  recipe: TrainRecipeDialogRecipe | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const DEFAULT_SYMBOL = 'BTC/USDC';
const DEFAULT_TF = '1h';
const DEFAULT_WINDOW_BARS = 2000;
const MIN_WINDOW_BARS = 1500;

type Phase = 'idle' | 'starting' | 'polling' | 'done' | 'error';

export function TrainRecipeDialog({ recipe, open, onOpenChange }: TrainRecipeDialogProps) {
  const qc = useQueryClient();
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const [tf, setTf] = useState(DEFAULT_TF);
  const [windowBars, setWindowBars] = useState(DEFAULT_WINDOW_BARS);
  const [phase, setPhase] = useState<Phase>('idle');
  const [jobId, setJobId] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  // Réinitialise l'état local à chaque ouverture : si l'utilisateur ferme
  // puis rouvre pour une autre recette, on ne veut pas conserver le job_id
  // précédent (la barre de progression serait figée sur un ancien run).
  useEffect(() => {
    if (!open) {
      setPhase('idle');
      setJobId(null);
      setStartError(null);
    } else if (recipe) {
      // Réinitialise aussi les inputs à leurs défauts à l'ouverture —
      // évite qu'un utilisateur reprenne en route les valeurs d'un run
      // précédent sans s'en rendre compte.
      setSymbol(DEFAULT_SYMBOL);
      setTf(DEFAULT_TF);
      setWindowBars(DEFAULT_WINDOW_BARS);
      setPhase('idle');
      setJobId(null);
      setStartError(null);
    }
  }, [open, recipe]);

  // Polling du statut du job via `useMLTrainStatus` : le hook se désactive
  // (`enabled: !!jobId`) tant qu'aucun job n'a démarré, et coupe lui-même
  // son `refetchInterval` quand `status !== 'running'`.
  const { data: jobStatus } = useMLTrainStatus(jobId);

  // Surveille la transition `running` → `done`/`error` pour le toast final
  // et l'invalidation des queries ML consommées par le tab ML.
  useEffect(() => {
    if (!jobStatus || phase !== 'polling') return;
    if (jobStatus.status === 'done') {
      setPhase('done');
      toast.success('Modèle entraîné', {
        description: `${recipe?.recipe ?? 'Recette'} · ${tf} · ${symbol}`,
      });
      // Invalide les queries consommées par le tab ML pour que la
      // StrategyTable (statut Entraîné/Non entraîné, AUC) se rafraîchisse
      // sans attendre le prochain poll de 30 s. La query key réelle est
      // `['mlInfo']` (et non `['ml-strategy-info']` mentionné dans la spec
      // — c'est le nom utilisé dans `useMLStrategyInfo`).
      qc.invalidateQueries({ queryKey: ['mlInfo'] });
      qc.invalidateQueries({ queryKey: ['ml-recipes'] });
    } else if (jobStatus.status === 'error') {
      setPhase('error');
      toast.error('Entraînement échoué', {
        description: jobStatus.error ?? 'Erreur inconnue',
      });
    }
  }, [jobStatus, phase, qc, recipe, tf, symbol]);

  // Soumission : appelle `api.startMLTrain` puis bascule en phase polling
  // avec le `job_id` retourné. On ne lance pas si la recette n'est pas
  // `trainable` (le bouton est désactivé dans l'UI, mais on garde la
  // garde-fou côté handler pour le cas où le dialog serait ouvert par
  // programmation avec une recette non trainable).
  const handleStart = async () => {
    if (!recipe) return;
    if (recipe.trainable === false) {
      toast.error('Recette non entraînable', {
        description: recipe.reason ?? 'Aucune raison fournie',
      });
      return;
    }
    if (!symbol.trim() || !tf.trim()) {
      toast.error('Symbole et timeframe requis');
      return;
    }
    if (windowBars < MIN_WINDOW_BARS) {
      toast.error(`Minimum ${MIN_WINDOW_BARS} bougies requis`);
      return;
    }
    setPhase('starting');
    setStartError(null);
    try {
      const res = await api.startMLTrain({
        strategy: recipe.recipe,
        symbol: symbol.trim(),
        tf: tf.trim(),
        window_bars: windowBars > 0 ? windowBars : null,
      });
      setJobId(res.job_id);
      setPhase('polling');
    } catch (e: any) {
      setPhase('error');
      setStartError(e?.message ?? 'Erreur inconnue');
      toast.error(`Erreur : ${e?.message ?? 'inconnue'}`);
    }
  };

  if (!recipe) return null;

  const trainable = recipe.trainable !== false;
  const heads: string[] = Array.isArray(recipe.heads) ? recipe.heads : [];
  const isRunning = phase === 'starting' || phase === 'polling';
  const status = jobStatus?.status;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Rocket className="w-4 h-4 text-purple-400" />
            Entraîner « {recipe.recipe} »
          </DialogTitle>
          <DialogDescription>
            Lance un entraînement LightGBM pour cette recette. Le modèle est
            sauvegardé automatiquement par le backend à la fin du run.
          </DialogDescription>
        </DialogHeader>

        {/* Récap de la recette — les badges reprennent ceux de `MLRecipesList`
            pour que l'utilisateur reconnaisse la recette qu'il a cliquée. */}
        <div className="rounded-lg border border-border bg-card-hover/50 p-3 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-sm font-semibold">{recipe.recipe}</span>
            {trainable ? (
              <Badge variant="success" className="text-[9px]">
                <CheckCircle2 className="w-3 h-3" />
                Entraînable
              </Badge>
            ) : (
              <Badge variant="danger" className="text-[9px]">
                <XCircle className="w-3 h-3" />
                Non entraînable
              </Badge>
            )}
          </div>
          {!trainable && recipe.reason && (
            <p className="text-[10px] text-amber-400 italic">⚠ {recipe.reason}</p>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            {recipe.features_catalog && (
              <Badge variant="muted" className="text-[9px] font-mono">
                {recipe.features_catalog}
              </Badge>
            )}
            {recipe.label_scheme && (
              <Badge variant="purple" className="text-[9px] font-mono">
                {recipe.label_scheme}
              </Badge>
            )}
            {heads.length > 0 && (
              <Badge variant="info" className="text-[9px]">
                {heads.join(' · ')}
              </Badge>
            )}
          </div>
        </div>

        {/* Inputs : symbole, TF, bougies. Defaults BTC/USDC · 1h · 2000,
            minimum 1500 bougies (sous ce seuil, LightGBM n'a pas assez
            d'échantillons pour un entraînement fiable). */}
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="train-recipe-symbol">Symbole</Label>
            <Input
              id="train-recipe-symbol"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              disabled={isRunning}
              className="font-mono"
            />
          </div>
          <div className="space-y-1.5">
            <Label>Timeframe</Label>
            <TimeframeButtons
              value={tf}
              onChange={setTf}
              size="sm"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="train-recipe-window">
              Bougies
              <span className="block normal-case text-[10px] text-dim font-normal">
                min {MIN_WINDOW_BARS}
              </span>
            </Label>
            <Input
              id="train-recipe-window"
              type="number"
              min={MIN_WINDOW_BARS}
              value={windowBars}
              onChange={(e) =>
                setWindowBars(Math.max(MIN_WINDOW_BARS, Number(e.target.value) || MIN_WINDOW_BARS))
              }
              disabled={isRunning}
              className="font-mono"
            />
          </div>
        </div>

        {/* État du job : Idle / Démarrage / Running / Done / Error. On
            affiche le statut backend (`running`/`done`/`error`) dès qu'on
            a un `jobId`, sinon un message « Démarrage… » pendant l'appel
            initial à `startMLTrain`. */}
        {phase !== 'idle' && (
          <div
            className={cn(
              'rounded-lg border px-3 py-2 text-xs flex items-center gap-2',
              phase === 'done'
                ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                : phase === 'error'
                  ? 'border-red-500/30 bg-red-500/10 text-red-300'
                  : 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
            )}
            role="status"
            aria-live="polite"
          >
            {isRunning && <Loader2 className="w-3.5 h-3.5 animate-spin flex-shrink-0" />}
            {phase === 'done' && <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" />}
            {phase === 'error' && <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />}
            <div className="min-w-0">
              {phase === 'starting' && <span>Démarrage de l&apos;entraînement…</span>}
              {phase === 'polling' && (
                <div className="space-y-0.5">
                  <div>Entraînement en cours…</div>
                  {jobStatus && (
                    <div className="font-mono text-[10px] opacity-80">
                      job_id: {jobId} · status: {status ?? '—'}
                      {jobStatus.error ? ` · ${jobStatus.error}` : ''}
                    </div>
                  )}
                </div>
              )}
              {phase === 'done' && (
                <div className="space-y-0.5">
                  <div className="font-semibold">Modèle entraîné</div>
                  <div className="font-mono text-[10px] opacity-80">
                    job_id: {jobId}
                  </div>
                </div>
              )}
              {phase === 'error' && (
                <div className="space-y-0.5">
                  <div className="font-semibold">Échec de l&apos;entraînement</div>
                  <div className="font-mono text-[10px] opacity-80">
                    {startError ?? jobStatus?.error ?? 'Erreur inconnue'}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        <DialogFooter className="gap-2 sm:gap-2 mt-2 flex-col sm:flex-row sm:items-center sm:justify-between">
          {/* Lien secondaire vers le registre versionné : on le garde
              secondaire (outline) pour ne pas concurrencer le bouton
              principal, mais il reste visible — c'est le seul moyen
              d'accéder à la gate de promotion / pin / sweep. */}
          <Link
            href={`/models?recipe=${encodeURIComponent(recipe.recipe)}`}
            className="text-[11px] text-muted hover:text-foreground inline-flex items-center gap-1 underline-offset-2 hover:underline"
          >
            Voir dans le registre
            <ExternalLink className="w-3 h-3" />
          </Link>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={isRunning}
            >
              {phase === 'done' ? 'Fermer' : 'Annuler'}
            </Button>
            <Button
              variant="primary"
              onClick={handleStart}
              disabled={isRunning || phase === 'done' || !trainable}
            >
              {isRunning ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Rocket className="w-4 h-4" />
              )}
              Lancer l&apos;entraînement
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
