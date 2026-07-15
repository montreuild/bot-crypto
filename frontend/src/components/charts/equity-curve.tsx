'use client';

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis, CartesianGrid } from 'recharts';
import { useBotStatus } from '@/hooks/use-api';
import { useMemo } from 'react';

export function EquityCurve() {
  const { data: status } = useBotStatus();

  // Construit une equity curve simulée basée sur le PnL total
  // (le backend n'expose pas encore l'historique d'equity — TODO: utiliser /api/stats/daily)
  const chartData = useMemo(() => {
    if (!status) return [];
    const baseCapital = status.capital || 1000;
    const totalPnl = status.total_pnl || 0;
    // Génère 30 points pour la démo
    const points = 30;
    return Array.from({ length: points }, (_, i) => {
      const progress = i / (points - 1);
      const noise = Math.sin(i * 0.5) * 0.005 + Math.cos(i * 0.3) * 0.003;
      const equity = baseCapital + (totalPnl * progress) + (baseCapital * noise);
      return {
        time: `${i}h`,
        equity: Math.round(equity * 100) / 100,
      };
    });
  }, [status]);

  const currentEquity = chartData[chartData.length - 1]?.equity || 0;
  const startEquity = chartData[0]?.equity || 0;
  const delta = currentEquity - startEquity;
  const deltaPct = startEquity > 0 ? (delta / startEquity) * 100 : 0;
  const color = delta >= 0 ? '#10b981' : '#ef4444';

  return (
    <Card>
      <CardHeader>
        <CardTitle>Equity Curve</CardTitle>
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm font-semibold">
            ${currentEquity.toFixed(2)}
          </span>
          <span
            className="text-xs font-mono"
            style={{ color }}
          >
            {delta >= 0 ? '+' : ''}{delta.toFixed(2)} ({deltaPct.toFixed(2)}%)
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={color} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
              <XAxis
                dataKey="time"
                stroke="#6b7280"
                fontSize={10}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                stroke="#6b7280"
                fontSize={10}
                tickLine={false}
                axisLine={false}
                domain={['auto', 'auto']}
                tickFormatter={(v) => `$${v.toFixed(0)}`}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#141a23',
                  border: '1px solid #1f2937',
                  borderRadius: '8px',
                  fontSize: '12px',
                }}
                labelStyle={{ color: '#9ca3af' }}
                formatter={(value: any) => [`$${Number(value).toFixed(2)}`, 'Equity']}
              />
              <Area
                type="monotone"
                dataKey="equity"
                stroke={color}
                strokeWidth={2}
                fill="url(#equityGradient)"
                animationDuration={500}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
