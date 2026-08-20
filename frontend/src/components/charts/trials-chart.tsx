'use client';

/**
 * P1-3 — Courbe d'apprentissage des trials d'optimisation.
 *
 * Affiche l'évolution du `final_score` et de l'`overfit` ratio au fil des
 * essais d'optimisation via un LineChart recharts. Permet à l'utilisateur
 * de visualiser la convergence de l'optimiseur (bayésien/random/grid) et
 * de détecter un surapprentissage précoce (overfit qui monte).
 *
 * Données : `job.trials` (tableau de `{trial, oos_pnl, oos_sharpe, final_score, overfit}`).
 * Si < 3 trials, n'affiche rien (pas assez de points pour une courbe lisible).
 */

import { useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceLine, Legend,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { TrendingUp } from 'lucide-react';
import type { OptimizeTrial } from '@/types';

interface Props {
  /** Trials du job (déjà présents dans `OptimizeJob.trials`). */
  trials?: OptimizeTrial[];
  className?: string;
  nTotal?: number;
  topOnly?: boolean;
}

export function TrialsChart({ trials, className, nTotal, topOnly }: Props) {
  // Transforme les trials en données pour recharts
  const data = useMemo(() => {
    if (!Array.isArray(trials) || trials.length < 3) return [];
    return trials.map((t, i) => ({
      trial: i + 1,
      final_score: typeof t.final_score === 'number' ? t.final_score
        : typeof t.final === 'number' ? t.final
        : null,
      oos_score: typeof t.oos_score === 'number' ? t.oos_score : null,
      overfit: typeof t.overfit === 'number' ? t.overfit : null,
      oos_pnl: typeof t.oos_pnl === 'number' ? t.oos_pnl : null,
    }));
  }, [trials]);

  if (data.length < 3) {
    return null; // pas assez de points pour une courbe lisible
  }

  // Calcul du best score cumulé (meilleur score vu jusqu'à présent)
  let bestSoFar = -Infinity;
  const dataWithBest = data.map((d) => {
    if (d.final_score != null && d.final_score > bestSoFar) {
      bestSoFar = d.final_score;
    }
    return { ...d, best_so_far: bestSoFar > -Infinity ? bestSoFar : null };
  });

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <TrendingUp className="w-4 h-4 text-cyan-400" />
          Courbe d&apos;apprentissage
          <span className="text-[10px] text-muted font-normal">
            {topOnly
              ? `${data.length} meilleurs trials (sur ${nTotal ?? data.length})`
              : `${data.length} trials${nTotal && nTotal > data.length ? ` / ${nTotal}` : ''}`}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-48 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={dataWithBest} margin={{ top: 5, right: 5, bottom: 0, left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
              <XAxis
                dataKey="trial"
                stroke="#6b7280"
                fontSize={10}
                tickLine={false}
                axisLine={false}
                label={{ value: 'Trial #', position: 'insideBottom', offset: -2, fontSize: 10, fill: '#6b7280' }}
              />
              <YAxis
                yAxisId="left"
                stroke="#6b7280"
                fontSize={10}
                tickLine={false}
                axisLine={false}
                domain={['auto', 'auto']}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                stroke="#6b7280"
                fontSize={10}
                tickLine={false}
                axisLine={false}
                domain={[0, 'auto']}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#141a23',
                  border: '1px solid #1f2937',
                  borderRadius: '8px',
                  fontSize: '12px',
                }}
                labelStyle={{ color: '#9ca3af' }}
                labelFormatter={(v) => `Trial #${v}`}
              />
              <Legend
                wrapperStyle={{ fontSize: '10px' }}
                iconType="line"
              />
              {/* Zone rouge au-dessus de overfit=2.5 (surapprentissage probable) */}
              <ReferenceLine
                yAxisId="right"
                y={2.5}
                stroke="#ef4444"
                strokeDasharray="4 2"
                fontSize={9}
                label={{ value: 'overfit 2.5', fill: '#ef4444', fontSize: 9, position: 'right' }}
              />
              {/* Score final par trial (transient) */}
              <Line
                yAxisId="left"
                type="monotone"
                dataKey="final_score"
                stroke="#4d9fff"
                strokeWidth={1}
                dot={false}
                name="Final score"
                animationDuration={300}
              />
              {/* Best score cumulé (convergence) */}
              <Line
                yAxisId="left"
                type="monotone"
                dataKey="best_so_far"
                stroke="#22c55e"
                strokeWidth={2}
                dot={false}
                name="Best so far"
                animationDuration={300}
              />
              {/* Overfit ratio (axe droit) */}
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="overfit"
                stroke="#f59e0b"
                strokeWidth={1}
                strokeDasharray="3 2"
                dot={false}
                name="Overfit"
                animationDuration={300}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <p className="text-[10px] text-dim mt-2">
          <span className="text-emerald-400">━ Best so far</span> : meilleur score
          cumulé (convergence).{' '}
          <span className="text-blue-400">━ Final score</span> : score par trial.{' '}
          <span className="text-amber-400">┄ Overfit</span> : ratio IS/OOS
          (&gt; 2.5 = surapprentissage probable, ligne rouge).
        </p>
      </CardContent>
    </Card>
  );
}
