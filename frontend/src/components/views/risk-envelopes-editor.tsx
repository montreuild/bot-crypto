'use client';

/**
 * S12 — enveloppes par venue (Capital) + faisabilité (Risque).
 *
 * Les venues affichées = union de venues.defs (déclarées) et risk.envelopes.
 * Champs de risque grisés hors preset « Personnalisé ».
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  useConfig,
  usePresets,
  useRisk,
  useRiskDiagnostics,
  useUpdateEnvelopes,
} from '@/hooks/use-api';
import type { VenueEnvelopeConfig } from '@/types';
import { cn } from '@/lib/utils';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { AlertTriangle, Info, Lock, Save } from 'lucide-react';

function formatFeasibilityAt(ms: number | undefined): string {
  if (!ms || !Number.isFinite(ms)) return '—';
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'short',
      timeStyle: 'medium',
    }).format(new Date(ms));
  } catch {
    return new Date(ms).toLocaleString();
  }
}

/** Champs S12 d'une enveloppe venue (capital + budgets de risque). */
const FIELDS: {
  key: keyof VenueEnvelopeConfig;
  label: string;
  hint: string;
  pct?: boolean;
  riskField?: boolean;
}[] = [
  { key: 'capital', label: 'Capital', hint: 'enveloppe de la venue, dans sa devise' },
  {
    key: 'max_symbol_exposure_pct',
    label: 'Exposition max / symbole',
    hint: '% du capital venue (notionnel max par symbole)',
    pct: true,
    riskField: true,
  },
  {
    key: 'symbol_risk_pct',
    label: 'Budget de risque / symbole',
    hint: 'perte max sur un symbole — doit être ≥ risque/trade (profile)',
    pct: true,
    riskField: true,
  },
  {
    key: 'venue_risk_pct',
    label: 'Budget de risque / venue',
    hint: 'perte max sur le livre — doit être ≥ budget symbole',
    pct: true,
    riskField: true,
  },
];

// ── Faisabilité (page Risque) ───────────────────────────────────────────────

export function RiskFeasibilityPanel() {
  const diagnosticsQuery = useRiskDiagnostics();
  const { data: diagnostics } = diagnosticsQuery;
  const feasibilityAt = formatFeasibilityAt(diagnosticsQuery.dataUpdatedAt);
  const errors = (diagnostics?.diagnostics ?? []).filter((d) => d.severity === 'error');
  const warnings = (diagnostics?.diagnostics ?? []).filter((d) => d.severity === 'warning');
  const alertedKey = useRef<string>('');

  // Alerte toast dès qu'un bloquant / avertissement apparaît (évite de rater
  // la section si l'utilisateur n'y regarde pas).
  useEffect(() => {
    if (!diagnostics) return;
    const key = `${diagnostics.errors}-${diagnostics.warnings}-${diagnosticsQuery.dataUpdatedAt}`;
    if (key === alertedKey.current) return;
    alertedKey.current = key;
    if (errors.length > 0) {
      toast.error(
        `Faisabilité risque : ${errors.length} bloquant(s)` +
          (warnings.length ? `, ${warnings.length} avertissement(s)` : ''),
        {
          description: errors[0]?.message,
          duration: 12000,
        },
      );
    } else if (warnings.length > 0) {
      toast.warning(`Faisabilité risque : ${warnings.length} avertissement(s)`, {
        description: warnings[0]?.message,
        duration: 10000,
      });
    }
  }, [diagnostics, diagnosticsQuery.dataUpdatedAt, errors, warnings]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Faisabilité de la configuration</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          {errors.length > 0 && (
            <Badge variant="danger" className="text-[10px]">{errors.length} bloquant(s)</Badge>
          )}
          {warnings.length > 0 && (
            <Badge variant="warning" className="text-[10px]">{warnings.length} avertissement(s)</Badge>
          )}
          {errors.length === 0 && warnings.length === 0 && diagnostics && (
            <Badge variant="success" className="text-[10px]">Aucun problème</Badge>
          )}
          <time
            className="text-[11px] text-dim font-mono tabular-nums"
            dateTime={
              diagnosticsQuery.dataUpdatedAt
                ? new Date(diagnosticsQuery.dataUpdatedAt).toISOString()
                : undefined
            }
            title="Dernière évaluation des diagnostics (heure locale)"
          >
            {feasibilityAt}
          </time>
        </div>
      </CardHeader>
      <CardContent>
        {diagnosticsQuery.isLoading && !diagnostics ? (
          <p className="text-xs text-muted">Évaluation en cours…</p>
        ) : errors.length === 0 && warnings.length === 0 ? (
          <p className="text-xs text-muted">
            Aucun slot n&apos;est structurellement empêché de trader. Ces contrôles sont
            analytiques : ils ne dépendent d&apos;aucune donnée de marché.
            {feasibilityAt !== '—' && (
              <> Évalué le <span className="font-mono">{feasibilityAt}</span>.</>
            )}
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
  );
}

// ── Enveloppes + venues déclarées (page Capital) ────────────────────────────

export function VenueEnvelopesEditor() {
  const { data: risk } = useRisk();
  const { data: config } = useConfig();
  const { data: presets } = usePresets();
  const update = useUpdateEnvelopes();
  const [draft, setDraft] = useState<Record<string, VenueEnvelopeConfig>>({});

  const isCustom = (presets?.current || '') === 'personnalise';
  const riskFieldsLocked = !isCustom;

  const envelopes: Record<string, VenueEnvelopeConfig> = useMemo(
    () => (risk as any)?.envelopes_config ?? {},
    [risk],
  );

  const venueDefs: Record<string, any> = useMemo(
    () => (config as any)?.venues?.defs ?? {},
    [config],
  );
  const defaultVenue = (config as any)?.venues?.default as string | undefined;

  // Venues = déclarées (defs) ∪ enveloppes — même liste que « Venues déclarées ».
  const venueNames = useMemo(() => {
    const names = new Set<string>([
      ...Object.keys(venueDefs),
      ...Object.keys(envelopes),
    ]);
    // default en premier, puis alpha
    const list = Array.from(names);
    list.sort((a, b) => {
      if (a === defaultVenue) return -1;
      if (b === defaultVenue) return 1;
      return a.localeCompare(b);
    });
    return list;
  }, [venueDefs, envelopes, defaultVenue]);

  const valueOf = (venue: string, key: keyof VenueEnvelopeConfig) =>
    draft[venue]?.[key] ?? envelopes[venue]?.[key];

  const setValue = (venue: string, key: keyof VenueEnvelopeConfig, raw: string) => {
    const n = Number(raw);
    if (Number.isNaN(n)) return;
    setDraft((d) => ({ ...d, [venue]: { ...(d[venue] || {}), [key]: n } }));
  };

  const dirty = Object.keys(draft).length > 0;

  const onSave = async () => {
    if (riskFieldsLocked) {
      // Capital seul peut être dirty — ok
      const onlyCapital = Object.values(draft).every((patch) =>
        Object.keys(patch).every((k) => k === 'capital'),
      );
      if (!onlyCapital) {
        toast.error('Passez en profil « Personnalisé » pour éditer les budgets de risque');
        return;
      }
    }
    try {
      await update.mutateAsync(draft);
      setDraft({});
      toast.success('Enveloppes enregistrées');
    } catch (e: any) {
      toast.error(e?.message ?? 'Édition refusée', { duration: 12000 });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Enveloppes par venue</CardTitle>
        <div className="flex items-center gap-2">
          {riskFieldsLocked ? (
            <Badge variant="muted" className="text-[10px]">
              <Lock className="w-3 h-3" />
              Risque verrouillé (preset)
            </Badge>
          ) : (
            <Badge variant="info" className="text-[10px]">Personnalisé — éditable</Badge>
          )}
          <Button onClick={onSave} disabled={!dirty || update.isPending} variant="outline" size="sm">
            <Save className="w-4 h-4" />
            {update.isPending ? '…' : 'Enregistrer'}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        <p className="text-[11px] text-muted">
          Liste alignée sur les <strong className="text-dim">venues déclarées</strong>
          {' '}(<code className="font-mono">venues.defs</code>) et leurs enveloppes S12
          (<code className="font-mono">risk.envelopes</code>). Les budgets de risque
          suivent le profil (Prudent / Équilibré / Agressif) sauf en mode Personnalisé.
        </p>

        {venueNames.length === 0 && (
          <p className="text-sm text-muted">Aucune venue déclarée dans la config.</p>
        )}

        {venueNames.map((venue) => {
          const def = venueDefs[venue] || {};
          const live = risk?.venues?.find((v) => v.venue === venue);
          const hasEnvelope = Boolean(envelopes[venue]);
          const provider =
            def.data_provider ||
            (def.exchange ? `ccxt/${def.exchange}` : undefined) ||
            '—';
          return (
            <div key={venue} className="space-y-3 p-3 rounded-lg border border-border bg-card-hover/40">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="font-mono text-xs font-semibold text-primary-400">{venue}</span>
                {defaultVenue === venue && (
                  <Badge variant="info" className="text-[9px]">default</Badge>
                )}
                {!hasEnvelope && (
                  <Badge variant="warning" className="text-[9px]">sans enveloppe</Badge>
                )}
                <span className="text-[10px] text-dim">
                  {def.market_type || '—'} · {def.asset_class || '—'} · {def.quote_currency || live?.currency || '—'}
                  {def.can_execute === false && ' · data-only'}
                  {' · '}
                  <span className="font-mono">{provider}</span>
                </span>
                {live && (
                  <span className="text-[10px] text-dim tabular-nums ml-auto">
                    {live.risk_engaged.toFixed(2)} / {live.risk_budget.toFixed(2)} {live.currency} engagés
                  </span>
                )}
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {FIELDS.map((f) => {
                  const locked = Boolean(f.riskField && riskFieldsLocked);
                  const v = valueOf(venue, f.key);
                  const changed = draft[venue]?.[f.key] !== undefined;
                  return (
                    <label key={f.key} className={cn('block', locked && 'opacity-60')}>
                      <span className="text-[11px] text-dim flex items-center gap-1">
                        {f.label}
                        {locked && <Lock className="w-3 h-3" />}
                      </span>
                      <input
                        type="number"
                        step={f.pct ? '0.005' : '100'}
                        min="0"
                        value={v ?? ''}
                        disabled={locked}
                        readOnly={locked}
                        onChange={(e) => {
                          if (locked) return;
                          setValue(venue, f.key, e.target.value);
                        }}
                        className={cn(
                          'w-full mt-1 px-2 py-1.5 rounded-md bg-card border text-sm font-mono',
                          locked && 'cursor-not-allowed bg-card-hover',
                          changed && !locked ? 'border-primary-400' : 'border-border',
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
          S12 : le budget symbole doit rester <strong className="text-dim">≥ risque par trade</strong>
          {' '}(<code className="font-mono">risk.profile</code>), et le budget venue ≥ budget symbole.
          Une édition qui rompt ces règles est refusée par l&apos;API.
        </p>
      </CardContent>
    </Card>
  );
}

/** @deprecated préférer VenueEnvelopesEditor + RiskFeasibilityPanel */
export function RiskEnvelopesEditor() {
  return (
    <div className="space-y-4">
      <RiskFeasibilityPanel />
      <VenueEnvelopesEditor />
    </div>
  );
}
