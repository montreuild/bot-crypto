'use client';

/**
 * S12 — édition des enveloppes par venue + panneau de diagnostics.
 *
 * Le backend rejoue la validation du chargement ET les diagnostics de
 * faisabilité avant d'écrire : une édition qui rendrait un slot incapable de
 * passer le moindre ordre répond 400 et ne touche à rien. Cet écran affiche
 * ce refus tel quel — le message porte déjà la valeur à appliquer.
 *
 * Le panneau de diagnostics est au-dessus du formulaire, pas en dessous :
 * c'est lui qui dit si la configuration actuelle est vivable, et on veut le
 * lire avant d'éditer, pas après avoir sauvegardé.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { useRisk, useRiskDiagnostics, useUpdateEnvelopes } from '@/hooks/use-api';
import type { VenueEnvelopeConfig } from '@/types';
import { cn } from '@/lib/utils';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import { AlertTriangle, Info, Save } from 'lucide-react';

const FIELDS: {
  key: keyof VenueEnvelopeConfig; label: string; hint: string; pct?: boolean;
}[] = [
  { key: 'capital', label: 'Capital', hint: 'enveloppe de la venue, dans sa devise' },
  { key: 'max_symbol_exposure_pct', label: 'Exposition max / symbole', hint: '% du capital de la venue', pct: true },
  { key: 'symbol_risk_pct', label: 'Budget de risque / symbole', hint: 'perte max sur un symbole, % de son enveloppe', pct: true },
  { key: 'venue_risk_pct', label: 'Budget de risque / venue', hint: 'perte max sur le livre, % du capital', pct: true },
];

export function RiskEnvelopesEditor() {
  const { data: risk } = useRisk();
  const { data: diagnostics } = useRiskDiagnostics();
  const update = useUpdateEnvelopes();
  const [draft, setDraft] = useState<Record<string, VenueEnvelopeConfig>>({});

  // Les valeurs SAISIES (pas les montants dérivés) : `/api/config` ne sert pas
  // le bloc `risk.envelopes`, `/api/risk` le porte explicitement pour cet écran.
  const envelopes: Record<string, VenueEnvelopeConfig> = useMemo(
    () => (risk as any)?.envelopes_config ?? {},
    [risk],
  );
  const venueNames = useMemo(() => {
    const fromConfig = Object.keys(envelopes);
    if (fromConfig.length > 0) return fromConfig;
    return (risk?.venues ?? []).map((v) => v.venue);
  }, [envelopes, risk]);

  const valueOf = (venue: string, key: keyof VenueEnvelopeConfig) =>
    draft[venue]?.[key] ?? envelopes[venue]?.[key];

  const setValue = (venue: string, key: keyof VenueEnvelopeConfig, raw: string) => {
    const n = Number(raw);
    if (Number.isNaN(n)) return;
    setDraft((d) => ({ ...d, [venue]: { ...d[venue], [key]: n } }));
  };

  const dirty = Object.keys(draft).length > 0;

  const onSave = async () => {
    try {
      await update.mutateAsync(draft);
      setDraft({});
      toast.success('Enveloppes enregistrées');
    } catch (e: any) {
      // Le détail du 400 porte la règle violée ET la valeur à appliquer —
      // le tronquer ferait perdre exactement l'information utile.
      toast.error(e?.message ?? 'Édition refusée', { duration: 12000 });
    }
  };

  const errors = (diagnostics?.diagnostics ?? []).filter((d) => d.severity === 'error');
  const warnings = (diagnostics?.diagnostics ?? []).filter((d) => d.severity === 'warning');

  return (
    <div className="space-y-4">
      {/* ── Diagnostics de faisabilité ────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Faisabilité de la configuration</CardTitle>
          <div className="flex gap-2">
            {errors.length > 0 && (
              <Badge variant="danger" className="text-[10px]">{errors.length} bloquant(s)</Badge>
            )}
            {warnings.length > 0 && (
              <Badge variant="warning" className="text-[10px]">{warnings.length} avertissement(s)</Badge>
            )}
            {errors.length === 0 && warnings.length === 0 && (
              <Badge variant="success" className="text-[10px]">Aucun problème</Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {errors.length === 0 && warnings.length === 0 ? (
            <p className="text-xs text-muted">
              Aucun slot n&apos;est structurellement empêché de trader. Ces contrôles sont
              analytiques : ils ne dépendent d&apos;aucune donnée de marché.
            </p>
          ) : (
            <ul className="space-y-2">
              {[...errors, ...warnings].map((d, i) => (
                <li
                  key={`${d.code}-${d.scope}-${i}`}
                  className={cn(
                    'flex gap-2 p-2 rounded-md border text-xs',
                    d.severity === 'error'
                      ? 'bg-red-500/5 border-red-500/30'
                      : 'bg-amber-500/5 border-amber-500/30',
                  )}
                >
                  {d.severity === 'error'
                    ? <AlertTriangle className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" />
                    : <Info className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />}
                  <div className="min-w-0">
                    <div className="font-mono text-[10px] text-dim">
                      {d.code} · {d.scope}
                    </div>
                    <div className="mt-0.5">{d.message}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ── Édition des enveloppes ────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Enveloppes par venue</CardTitle>
          <Button onClick={onSave} disabled={!dirty || update.isPending} variant="outline">
            <Save className="w-4 h-4" />
            {update.isPending ? 'Enregistrement…' : 'Enregistrer'}
          </Button>
        </CardHeader>
        <CardContent className="space-y-6">
          {venueNames.length === 0 && (
            <p className="text-sm text-muted">Aucune venue configurée.</p>
          )}
          {venueNames.map((venue) => {
            const live = risk?.venues?.find((v) => v.venue === venue);
            return (
              <div key={venue} className="space-y-3">
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-xs font-semibold text-primary-400">{venue}</span>
                  {live && (
                    <span className="text-[10px] text-dim tabular-nums">
                      {live.risk_engaged.toFixed(2)} / {live.risk_budget.toFixed(2)} {live.currency} engagés
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {FIELDS.map((f) => {
                    const v = valueOf(venue, f.key);
                    const changed = draft[venue]?.[f.key] !== undefined;
                    return (
                      <label key={f.key} className="block">
                        <span className="text-[11px] text-dim">{f.label}</span>
                        <input
                          type="number"
                          step={f.pct ? '0.005' : '100'}
                          min="0"
                          value={v ?? ''}
                          onChange={(e) => setValue(venue, f.key, e.target.value)}
                          className={cn(
                            'w-full mt-1 px-2 py-1.5 rounded-md bg-card border text-sm font-mono',
                            changed ? 'border-primary-400' : 'border-border',
                          )}
                        />
                        <span className="text-[10px] text-muted">
                          {f.hint}
                          {f.pct && v != null && ` — ${(Number(v) * 100).toFixed(2)} %`}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            );
          })}
          <p className="text-[11px] text-muted border-t border-border pt-3">
            Le budget de risque d&apos;un symbole doit rester{' '}
            <strong className="text-dim">au moins égal au risque par trade</strong> : un
            symbole réduit à un seul slot actif porte un poids de 1,0 et risquerait alors
            plus que son budget — aucun de ses ordres ne passerait. Une édition qui
            romprait cette règle est refusée, et le message indique la valeur minimale à
            appliquer.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
