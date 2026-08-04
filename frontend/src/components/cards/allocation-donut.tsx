'use client';

/**
 * Donut d'allocation — S12 : **par symbole d'abord, par slot ensuite**.
 *
 * L'ancienne version découpait le capital en N parts pour N slots, ce qui
 * donnait une image de diversification fausse : dix bots sur BTC ne sont pas
 * dix risques, c'est un pari BTC exprimé dix fois. L'anneau extérieur montre
 * donc l'enveloppe **par symbole** (la vraie diversification) et l'anneau
 * intérieur le poids des slots à l'intérieur de chaque symbole — pondéré par
 * confiance d'edge, jamais par performance ponctuelle.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMemo } from 'react';
import { useRisk } from '@/hooks/use-api';
import { parseSlotKey } from '@/lib/utils';

const COLORS = [
  '#06b6d4', '#10b981', '#8b5cf6', '#f59e0b', '#ef4444',
  '#3b82f6', '#ec4899', '#14b8a6', '#f97316', '#a855f7',
];

/** Fiche du bot correspondant — le drawer de /bots s'ouvre sur `?slot=`. */
const slotHref = (slotKey: string) => `/bots?slot=${encodeURIComponent(slotKey)}`;

export function AllocationDonut() {
  const router = useRouter();
  const { data } = useRisk();

  const { symbolRing, slotRing, currency, total } = useMemo(() => {
    const symbols = data?.symbols ?? [];
    const slots = data?.slots ?? [];
    const cur = symbols[0]?.currency ?? data?.venues?.[0]?.currency ?? '';
    const tot = symbols.reduce((s, x) => s + (x.envelope ?? 0), 0);

    const ring = symbols.map((s, i) => ({
      name: s.symbol,
      value: s.envelope ?? 0,
      color: COLORS[i % COLORS.length],
    }));

    const inner = slots
      .filter((s) => (s.weight ?? 0) > 0)
      .map((s) => {
        const idx = symbols.findIndex((x) => x.symbol === s.symbol && x.venue === s.venue);
        const { strategy, tf } = parseSlotKey(s.slot_key);
        return {
          name: `${strategy}/${tf}`,
          symbol: s.symbol,
          value: s.envelope ?? 0,
          weight: s.weight ?? 0,
          edge: s.edge_ci_low,
          color: COLORS[(idx < 0 ? 0 : idx) % COLORS.length],
          slotKey: s.slot_key,
        };
      })
      .sort((a, b) => b.value - a.value);

    return { symbolRing: ring, slotRing: inner, currency: cur, total: tot };
  }, [data]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Allocation</CardTitle>
        <span className="text-xs text-dim">par symbole, puis par bot</span>
      </CardHeader>
      <CardContent>
        {symbolRing.length === 0 ? (
          <div className="h-48 flex items-center justify-center text-sm text-muted">
            Aucune enveloppe active
          </div>
        ) : (
          <div className="h-48 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                {/* Anneau intérieur : les slots, colorés par symbole parent. */}
                <Pie
                  data={slotRing} cx="50%" cy="50%"
                  innerRadius={28} outerRadius={48} paddingAngle={1} dataKey="value"
                >
                  {slotRing.map((e, i) => (
                    <Cell
                      key={`slot-${i}`} fill={e.color} fillOpacity={0.45}
                      stroke="#0a0e14" strokeWidth={1}
                      className="cursor-pointer focus:outline-none"
                      onClick={() => router.push(slotHref(e.slotKey))}
                    />
                  ))}
                </Pie>
                {/* Anneau extérieur : les symboles — la vraie diversification. */}
                <Pie
                  data={symbolRing} cx="50%" cy="50%"
                  innerRadius={54} outerRadius={75} paddingAngle={2} dataKey="value"
                >
                  {symbolRing.map((e, i) => (
                    <Cell key={`sym-${i}`} fill={e.color} stroke="#0a0e14" strokeWidth={1.5} />
                  ))}
                </Pie>
                <Tooltip
                  content={({ active, payload }: any) => {
                    if (!active || !payload?.length) return null;
                    const p = payload[0].payload;
                    const pct = total > 0 ? (p.value / total) * 100 : 0;
                    return (
                      <div className="bg-card border border-border rounded-md p-2 text-xs">
                        <div className="font-medium">
                          {p.slotKey ? `${p.name} · ${p.symbol}` : p.name}
                        </div>
                        <div className="text-muted tabular-nums">
                          {p.value.toFixed(0)} {currency} · {pct.toFixed(1)}%
                        </div>
                        {p.slotKey && (
                          <div className="text-dim mt-1">
                            poids {(p.weight * 100).toFixed(1)}%
                            {p.edge != null && p.edge > 0
                              ? ` · edge ≥ ${p.edge.toFixed(2)}%`
                              : ' · edge non prouvée'}
                          </div>
                        )}
                      </div>
                    );
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}

        {slotRing.length > 0 && (
          <div className="mt-3 space-y-1.5 max-h-32 overflow-y-auto">
            {slotRing.map((d) => (
              <Link
                key={d.slotKey}
                href={slotHref(d.slotKey)}
                className="flex items-center gap-2 text-xs hover:bg-card-hover rounded px-1.5 py-0.5 transition-colors"
              >
                <span
                  className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                  style={{ backgroundColor: d.color }}
                />
                <span className="flex-1 truncate font-mono">{d.name}</span>
                <span className="text-muted tabular-nums">
                  {d.value.toFixed(0)} {currency}
                </span>
                {(d.edge == null || d.edge <= 0) && (
                  <span className="text-dim text-[10px]" title="edge non prouvée positive">
                    ⏳
                  </span>
                )}
              </Link>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
