'use client';

/**
 * S3-F1-US3 — Donut d'allocation par bot.
 *
 * Visualise la diversification du portefeuille. Clique sur un segment →
 * redirige vers la fiche bot. Légende avec budget réel vs shadow (cible).
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import Link from 'next/link';
import { useMemo } from 'react';
import { formatPct } from '@/lib/utils';
import type { SlotBudget } from '@/types';

interface AllocationDonutProps {
  slots: SlotBudget[] | undefined;
  capital: number;
  shadowAllocation?: Record<string, any>;
}

const COLORS = [
  '#06b6d4', '#10b981', '#8b5cf6', '#f59e0b', '#ef4444',
  '#3b82f6', '#ec4899', '#14b8a6', '#f97316', '#a855f7',
  '#84cc16', '#06b6d4', '#6366f1', '#d946ef',
];

export function AllocationDonut({ slots, capital, shadowAllocation }: AllocationDonutProps) {
  const data = useMemo(() => {
    if (!slots || slots.length === 0) return [];
    return slots
      .filter((s) => s.enabled && (s.budget_pct ?? 0) > 0)
      .map((s, i) => ({
        name: s.symbol ? `${s.strategy}/${s.tf}/${s.symbol}` : `${s.strategy}/${s.tf}`,
        slotKey: s.slot_key,
        value: s.budget_pct ?? 0,
        shadowValue: shadowAllocation?.[s.slot_key]?.budget_pct ?? s.budget_pct ?? 0,
        color: COLORS[i % COLORS.length],
        strategy: s.strategy,
        tf: s.tf,
        symbol: s.symbol,
      }))
      .sort((a, b) => b.value - a.value);
  }, [slots, shadowAllocation]);

  const totalAllocated = data.reduce((sum, d) => sum + d.value, 0);
  const reserve = Math.max(0, 1 - totalAllocated);

  // Ajoute la réserve (capital non alloué)
  const chartData = data.length > 0
    ? [...data, { name: 'Réserve', value: reserve, color: '#374151', slotKey: null, strategy: '', tf: '', symbol: '', shadowValue: 0 }]
    : [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Allocation par bot</CardTitle>
      </CardHeader>
      <CardContent>
        {chartData.length === 0 ? (
          <div className="h-48 flex items-center justify-center text-sm text-muted">
            Aucune allocation active
          </div>
        ) : (
          <div className="h-48 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={chartData}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={75}
                  paddingAngle={2}
                  dataKey="value"
                >
                  {chartData.map((entry, idx) => (
                    <Cell
                      key={`cell-${idx}`}
                      fill={entry.color}
                      stroke="#0a0e14"
                      strokeWidth={1.5}
                    />
                  ))}
                </Pie>
                <Tooltip
                  content={({ active, payload }: any) => {
                    if (!active || !payload?.length) return null;
                    const p = payload[0].payload;
                    return (
                      <div className="bg-card border border-border rounded-md p-2 text-xs">
                        <div className="font-medium">{p.name}</div>
                        <div className="text-muted">
                          {(p.value * 100).toFixed(1)}% · ${(p.value * capital).toFixed(0)}
                        </div>
                        {p.shadowValue !== p.value && p.slotKey && (
                          <div className="text-amber-400 mt-1">
                            Cible: {(p.shadowValue * 100).toFixed(1)}%
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

        {/* Légende */}
        {data.length > 0 && (
          <div className="mt-3 space-y-1.5 max-h-32 overflow-y-auto">
            {data.map((d, i) => (
              <Link
                key={d.slotKey}
                href={`/bots?slot=${encodeURIComponent(d.slotKey)}`}
                className="flex items-center gap-2 text-xs hover:bg-card-hover rounded px-1.5 py-0.5 transition-colors"
              >
                <span
                  className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                  style={{ backgroundColor: d.color }}
                />
                <span className="flex-1 truncate font-mono">
                  {d.symbol ? `${d.strategy}/${d.tf}/${d.symbol}` : `${d.strategy}/${d.tf}`}
                </span>
                <span className="text-muted">{(d.value * 100).toFixed(1)}%</span>
                {d.shadowValue !== d.value && (
                  <span className="text-amber-400 text-[10px]">
                    → {(d.shadowValue * 100).toFixed(1)}%
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
