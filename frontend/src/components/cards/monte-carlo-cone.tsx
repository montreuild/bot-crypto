'use client';

/**
 * S3-F3-US1 — Cône Monte-Carlo vs réel.
 *
 * Visualisation clé de la VISION_CIBLE_BOTS_AUTONOMES.md :
 * compare la performance live vs la simulation (forward-test).
 *
 * Bande IC 95% + marks sim (P50) + live + zero line.
 * Verdict coloré (ok / bad / na).
 *
 * Consomme /api/oos-tracker via le hook useOosTracker().
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis, ReferenceLine, Line } from 'recharts';
import { useMemo } from 'react';
import { cn, formatPct } from '@/lib/utils';

interface OosSlot {
  slot_key: string;
  ci_lower?: number[];
  ci_upper?: number[];
  median?: number[];
  live?: number[];
  labels?: string[];
  verdict?: 'ok' | 'bad' | 'na';
  n_trades?: number;
  edge?: number;
}

interface MonteCarloConeProps {
  slotKey: string;
  oosData: OosSlot | undefined;
  className?: string;
}

export function MonteCarloCone({ slotKey, oosData, className }: MonteCarloConeProps) {
  const chartData = useMemo(() => {
    if (!oosData || !oosData.median || !oosData.labels) return [];
    return oosData.labels.map((label, i) => ({
      label,
      median: oosData.median?.[i] ?? 0,
      ciLower: oosData.ci_lower?.[i] ?? 0,
      ciUpper: oosData.ci_upper?.[i] ?? 0,
      live: oosData.live?.[i] ?? null,
    }));
  }, [oosData]);

  if (!oosData || chartData.length === 0) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="text-sm">Cône Monte-Carlo</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-48 flex items-center justify-center text-sm text-muted">
            Pas encore de données OOS pour ce bot
          </div>
        </CardContent>
      </Card>
    );
  }

  const verdict = oosData.verdict || 'na';
  const verdictConfig = {
    ok: { label: '✓ Conforme à la simulation', color: 'text-emerald-400', bg: 'bg-emerald-500/10' },
    bad: { label: '⚠ Sous-performe la simulation', color: 'text-red-400', bg: 'bg-red-500/10' },
    na: { label: 'Données insuffisantes', color: 'text-amber-400', bg: 'bg-amber-500/10' },
  };
  const vc = verdictConfig[verdict];

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">Cône Monte-Carlo vs réel</CardTitle>
        <span className={cn('text-[10px] px-2 py-0.5 rounded font-medium', vc.bg, vc.color)}>
          {vc.label}
        </span>
      </CardHeader>
      <CardContent>
        <div className="h-48 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="mcConeGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#06b6d4" stopOpacity={0.05} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="label"
                stroke="#6b7280"
                fontSize={9}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                stroke="#6b7280"
                fontSize={9}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
              />
              <ReferenceLine y={0} stroke="#475569" strokeDasharray="2 2" />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#141a23',
                  border: '1px solid #1f2937',
                  borderRadius: '8px',
                  fontSize: '11px',
                }}
                labelStyle={{ color: '#9ca3af' }}
                formatter={(value: any, name: string) => {
                  const labels: Record<string, string> = {
                    median: 'Sim P50',
                    ciLower: 'IC 5%',
                    ciUpper: 'IC 95%',
                    live: 'Live',
                  };
                  return [`${(Number(value) * 100).toFixed(2)}%`, labels[name] || name];
                }}
              />
              <Area
                type="monotone"
                dataKey="ciUpper"
                stroke="none"
                fill="url(#mcConeGradient)"
                fillOpacity={1}
              />
              <Area
                type="monotone"
                dataKey="ciLower"
                stroke="none"
                fill="#0a0e14"
                fillOpacity={1}
              />
              <Line
                type="monotone"
                dataKey="median"
                stroke="#06b6d4"
                strokeWidth={2}
                strokeDasharray="4 2"
                dot={false}
              />
              {oosData.live && (
                <Line
                  type="monotone"
                  dataKey="live"
                  stroke={verdict === 'ok' ? '#10b981' : verdict === 'bad' ? '#ef4444' : '#f59e0b'}
                  strokeWidth={2.5}
                  dot={false}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-2 flex items-center justify-between text-[10px] text-dim">
          <span>n = {oosData.n_trades || 0} trades OOS</span>
          {oosData.edge != null && (
            <span className="font-mono">
              edge: {formatPct(oosData.edge)}
            </span>
          )}
        </div>
        <p className="mt-2 text-[10px] text-muted leading-relaxed">
          Le cône représente l&apos;intervalle de confiance à 95% de la simulation.
          La ligne live doit rester dans le cône pour confirmer la robustesse hors-échantillon.
        </p>
      </CardContent>
    </Card>
  );
}
