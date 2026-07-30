'use client';

/**
 * S3-F3-US3 — Bandeau HALT avec bouton « Acquitter le kill-switch ».
 *
 * Affiché en haut de la page Portefeuille quand le risk manager est en HALT.
 * Bouton « Acquitter » qui appelle /api/risk/reset-halt?force=true avec
 * confirmation modale (action critique).
 *
 * Avant : pas de bandeau dédié, le HALT était enfoui dans RiskPanel.
 */

import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { AlertOctagon, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { useQueryClient } from '@tanstack/react-query';

interface HaltBannerProps {
  halted: boolean;
  reason?: string;
  killSwitch?: boolean;
}

export function HaltBanner({ halted, reason, killSwitch }: HaltBannerProps) {
  const qc = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  if (!halted) return null;

  const handleAcknowledge = async () => {
    setLoading(true);
    try {
      // kill-switch persistant (sticky) nécessite force=true
      await api.resetHalt(!!killSwitch);
      toast.success('HALT acquitté, trading repris');
      qc.invalidateQueries({ queryKey: ['status'] });
      qc.invalidateQueries({ queryKey: ['portfolio'] });
      qc.invalidateQueries({ queryKey: ['circuitBreakers'] });
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Card className="border-red-500/40 bg-red-500/10">
        <CardContent className="p-4 flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-red-500/20 border border-red-500/40 flex items-center justify-center flex-shrink-0">
            <AlertOctagon className="w-5 h-5 text-red-400" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-0.5">
              <span className="font-semibold text-red-400 text-sm uppercase tracking-wider">
                HALT Actif
              </span>
              {killSwitch && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/20 text-red-300 font-mono">
                  KILL-SWITCH (sticky)
                </span>
              )}
            </div>
            <p className="text-sm text-foreground">
              {reason || 'Le trading est en pause. Le bot n\'émet plus de signaux.'}
              {killSwitch && (
                <span className="block text-xs text-muted mt-1">
                  Le kill-switch est persistant — l&apos;acquittement nécessite une confirmation explicite.
                </span>
              )}
            </p>
          </div>
          <Button
            variant="danger"
            onClick={() => setDialogOpen(true)}
            disabled={loading}
            className="flex-shrink-0"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
            Acquitter
          </Button>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        title="Acquitter le HALT ?"
        description={
          killSwitch
            ? 'Le kill-switch est persistant (sticky). L\'acquitter reprendra le trading immédiatement. Cette action est journalisée dans l\'audit log.'
            : 'Le trading reprendra immédiatement. Cette action est journalisée dans l\'audit log.'
        }
        confirmLabel="Acquitter et reprendre"
        variant="danger"
        isLoading={loading}
        onConfirm={handleAcknowledge}
      />
    </>
  );
}
