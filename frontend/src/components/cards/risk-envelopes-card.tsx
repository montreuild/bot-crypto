'use client';

/**
 * S12 — Enveloppes & risque : jauges imbriquées venue → symbole → slot.
 *
 * Règle d'affichage de toute cette carte : **un montant de risque ne s'affiche
 * jamais sans sa base**. Un « 12,40 € engagés » ne veut rien dire seul ; c'est
 * précisément ce qui a laissé passer la divergence backtest 1 000 € / live
 * 90 € sans que rien ne la signale. Chaque barre porte donc engagé / plafond,
 * et les deux plafonds — enveloppe (notionnel) et budget de risque (perte) —
 * sont montrés côte à côte parce qu'ils ne se remplacent pas.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { useRisk } from '@/hooks/use-api';
import { cn, parseSlotKey } from '@/lib/utils';
import type { RiskSlot } from '@/types';
import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

const fmt = (v: number | null | undefined, currency?: string) =>
  `${(v ?? 0).toFixed(2)}${currency ? ` ${currency}` : ''}`;

/** Vert sous 50 %, ambre au-delà de 80 % : au-delà, le prochain trade sera
 *  refusé — c'est une information d'exploitation, pas une décoration. */
function usageColor(ratio: number): string {
  if (ratio >= 0.8) return 'bg-amber-400';
  if (ratio >= 0.5) return 'bg-cyan-400';
  return 'bg-emerald-400';
}

function Gauge({ label, used, max, currency, hint }: {
  label: string; used: number; max: number; currency?: string; hint?: string;
}) {
  const ratio = max > 0 ? Math.min(used / max, 1) : 0;
  return (
    <div>
      <div className="flex justify-between items-baseline text-[10px] text-dim mb-1">
        <span>{label}</span>
        <span className="font-mono tabular-nums">
          {fmt(used)} <span className="text-muted">/ {fmt(max, currency)}</span>
          {max > 0 && <span className="ml-1 text-muted">({(ratio * 100).toFixed(0)}%)</span>}
        </span>
      </div>
      <div className="h-1.5 bg-card rounded-full overflow-hidden" role="presentation">
        <div
          className={cn('h-full rounded-full transition-all', usageColor(ratio))}
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
      {hint && <div className="text-[10px] text-muted mt-0.5">{hint}</div>}
    </div>
  );
}

function SlotRow({ slot }: { slot: RiskSlot }) {
  const { strategy, tf } = parseSlotKey(slot.slot_key);
  const proven = slot.edge_ci_low != null && slot.edge_ci_low > 0;
  return (
    <div className="pl-4 py-1.5 border-l border-border/40">
      <div className="flex justify-between items-baseline gap-2">
        <span className="font-mono text-[11px] truncate">
          {strategy}<span className="text-muted">/{tf}</span>
        </span>
        <span className="text-[10px] text-dim shrink-0 tabular-nums">
          poids {(slot.weight * 100).toFixed(1)}% · {fmt(slot.envelope, slot.currency)}
        </span>
      </div>
      <div className="mt-1">
        <Gauge
          label="risque engagé"
          used={slot.risk_engaged}
          max={slot.risk_amount}
          currency={slot.currency}
        />
      </div>
      <div className="text-[10px] mt-0.5">
        {proven ? (
          <span className="text-emerald-400">
            edge ≥ {(slot.edge_ci_low as number).toFixed(2)}%
          </span>
        ) : (
          <span className="text-muted">edge non prouvée — poids plancher</span>
        )}
      </div>
    </div>
  );
}

export function RiskEnvelopesCard() {
  const { data, isLoading } = useRisk();
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const slotsBySymbol = useMemo(() => {
    const out: Record<string, RiskSlot[]> = {};
    for (const s of data?.slots ?? []) {
      (out[`${s.venue}::${s.symbol}`] ??= []).push(s);
    }
    return out;
  }, [data]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-sm">Enveloppes &amp; risque</CardTitle></CardHeader>
        <CardContent><div className="h-32 animate-pulse bg-card-hover rounded" /></CardContent>
      </Card>
    );
  }

  const venues = data?.venues ?? [];
  const currencies = new Set(venues.map((v) => v.currency).filter(Boolean));
  const singleCurrency = currencies.size === 1 ? [...currencies][0] : null;

  if (venues.length === 0) {
    return (
      <Card>
        <CardHeader><CardTitle className="text-sm">Enveloppes &amp; risque</CardTitle></CardHeader>
        <CardContent>
          <div className="text-center py-8 text-sm text-muted">
            Aucune enveloppe active — aucun slot ne trade.
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Enveloppes &amp; risque</CardTitle>
        {/* Un total n'a de sens que si toutes les venues partagent la devise :
            additionner des EUR et des USDC pour l'afficher sous un seul
            libellé serait exactement la lecture trompeuse que S12 supprime. */}
        {singleCurrency ? (
          <span className="text-xs text-dim tabular-nums">
            {fmt(data?.total_risk_engaged, singleCurrency)} engagés
          </span>
        ) : (
          <span className="text-xs text-dim">
            {venues.length} venues · devises distinctes
          </span>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {venues.map((v) => {
          const symbols = (data?.symbols ?? []).filter((s) => s.venue === v.venue);
          return (
            <div key={v.venue} className="space-y-2">
              <div className="font-mono text-xs font-semibold text-primary-400">{v.venue}</div>
              <Gauge
                label="notionnel / enveloppe venue"
                used={v.notional_engaged} max={v.envelope} currency={v.currency}
              />
              <Gauge
                label="risque / budget venue"
                used={v.risk_engaged} max={v.risk_budget} currency={v.currency}
                hint="borne la perte si tous les stops sautent en même temps"
              />

              {symbols.map((sym) => {
                const key = `${sym.venue}::${sym.symbol}`;
                const slots = slotsBySymbol[key] ?? [];
                const isOpen = open[key] ?? false;
                return (
                  <div key={key} className="ml-3 mt-2 space-y-1.5">
                    <button
                      onClick={() => setOpen((o) => ({ ...o, [key]: !isOpen }))}
                      aria-expanded={isOpen}
                      className="flex items-center gap-1 text-xs font-medium hover:text-primary-400 transition-colors"
                    >
                      {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      {sym.symbol}
                      <span className="text-[10px] text-muted font-normal">
                        ({slots.length} slot{slots.length > 1 ? 's' : ''})
                      </span>
                    </button>
                    <Gauge
                      label="risque / budget symbole"
                      used={sym.risk_engaged} max={sym.risk_budget} currency={sym.currency}
                      hint={
                        slots.length > 1
                          ? 'partagé par tous les bots du symbole — ce sont des quasi-substituts'
                          : undefined
                      }
                    />
                    {isOpen && (
                      <div className="space-y-1 mt-1">
                        {slots.map((s) => <SlotRow key={s.slot_key} slot={s} />)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
