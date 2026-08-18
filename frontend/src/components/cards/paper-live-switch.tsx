'use client';

/**
 * E3-F1-US8 — Bascule Paper ↔ Live avec avertissement critique.
 *
 * User Story **Must** de l'audit, jamais implémentée et absente des « réserves
 * non traitées » du rapport : `/config` se contentait d'AFFICHER un badge
 * `paper_mode` en lecture seule, et aucun appel à `POST /api/config/trading`
 * n'existait dans le frontend. Passer en argent réel supposait donc d'éditer
 * `config.yaml` à la main.
 *
 * `ConfirmDialog` ne convenait pas : l'audit exige une DOUBLE confirmation
 * (case à cocher + bouton), que ce composant générique ne sait pas porter.
 *
 * Asymétrie assumée : passer en LIVE (argent réel) demande la case à cocher ;
 * revenir en PAPER est un retour à la sécurité et ne demande qu'un clic.
 */

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { useUpdateTradingConfig } from '@/hooks/use-api';
import { toast } from 'sonner'
import { errorMessage } from '@/lib/utils';
import { AlertTriangle, Loader2, ShieldCheck } from 'lucide-react';

export function PaperLiveSwitch({ paperMode }: { paperMode: boolean | undefined }) {
  const [open, setOpen] = useState(false);
  const [ack, setAck] = useState(false);
  const mutation = useUpdateTradingConfig();

  // `paper_mode` inconnu (config pas encore chargée) → on n'affirme rien et on
  // n'offre pas de bascule : c'est le même repli prudent que celui retenu pour
  // le HealthBanner (R8), où affirmer « live » par défaut était trompeur.
  if (paperMode === undefined || paperMode === null) return null;

  const goingLive = paperMode; // on est en paper → la bascule active le live

  const handleOpenChange = (v: boolean) => {
    setOpen(v);
    if (!v) setAck(false);
  };

  const handleConfirm = async () => {
    try {
      await mutation.mutateAsync({ paper_mode: !paperMode });
      toast.success(goingLive ? 'Mode LIVE activé — trading en argent réel' : 'Mode PAPER rétabli');
      handleOpenChange(false);
    } catch (e) {
      toast.error(`Bascule impossible : ${errorMessage(e) || e}`);
    }
  };

  return (
    <>
      <div className="flex items-center justify-between gap-3">
        <span className="text-muted">Paper mode</span>
        <div className="flex items-center gap-2">
          <Badge variant={paperMode ? 'success' : 'danger'}>
            {paperMode ? 'OUI' : 'NON — LIVE RÉEL'}
          </Badge>
          <Button
            size="sm"
            variant={goingLive ? 'danger' : 'outline'}
            onClick={() => setOpen(true)}
          >
            {goingLive ? 'Passer en LIVE' : 'Revenir en PAPER'}
          </Button>
        </div>
      </div>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {goingLive ? (
                <>
                  <AlertTriangle className="w-5 h-5 text-red-500" />
                  Activer le TRADING LIVE RÉEL ?
                </>
              ) : (
                <>
                  <ShieldCheck className="w-5 h-5 text-emerald-500" />
                  Revenir en mode PAPER ?
                </>
              )}
            </DialogTitle>
            <DialogDescription>
              {goingLive
                ? 'Le bot passera d’ordres réels sur l’exchange, avec de l’argent réel.'
                : 'Le bot repassera en simulation. Aucun ordre réel ne sera transmis à l’exchange.'}
            </DialogDescription>
          </DialogHeader>

          {goingLive && (
            <div className="space-y-3">
              <div className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-300 space-y-1">
                <p className="font-semibold text-red-400">⚠ LIVE TRADING RÉEL</p>
                <ul className="list-disc list-inside space-y-0.5">
                  <li>Les ordres seront exécutés avec vos fonds réels.</li>
                  <li>Les pertes sont réelles et définitives.</li>
                  <li>Vérifiez vos limites de risque et vos circuit breakers avant de continuer.</li>
                  <li>Les positions déjà ouvertes en simulation ne sont pas transférées.</li>
                </ul>
              </div>
              <label className="flex items-start gap-2 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  checked={ack}
                  onChange={(e) => setAck(e.target.checked)}
                  className="mt-0.5 accent-red-500"
                />
                <span>
                  Je comprends que le bot va trader avec de l’argent réel et j’en assume
                  la responsabilité.
                </span>
              </label>
            </div>
          )}

          <DialogFooter className="gap-2 sm:gap-2 mt-4">
            <Button variant="ghost" onClick={() => handleOpenChange(false)} disabled={mutation.isPending}>
              Annuler
            </Button>
            <Button
              className={goingLive
                ? 'bg-red-600 hover:bg-red-700 text-white'
                : 'bg-emerald-600 hover:bg-emerald-700 text-white'}
              onClick={handleConfirm}
              disabled={mutation.isPending || (goingLive && !ack)}
            >
              {mutation.isPending && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
              {goingLive ? 'Activer le LIVE' : 'Revenir en PAPER'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
