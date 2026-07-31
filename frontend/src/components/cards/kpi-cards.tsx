'use client';

import { Card } from '@/components/ui/card';
import { cn, formatUSD, formatPct, formatNumber } from '@/lib/utils';
import { LucideIcon, TrendingUp, TrendingDown, Wallet, Activity, Percent, Trophy, Zap } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

interface KPICardProps {
  label: string;
  value: number | string;
  sublabel?: string;
  icon?: LucideIcon;
  trend?: 'up' | 'down' | 'neutral';
  trendValue?: string;
  flashOnChange?: boolean;
  format?: 'usd' | 'pct' | 'number';
}

export function KPICard({
  label, value, sublabel, icon: Icon, trend, trendValue, flashOnChange = true, format = 'usd',
}: KPICardProps) {
  const [flashClass, setFlashClass] = useState('');

  // S0-F1-US3 — `prevValue` était capturé une seule fois au premier render via
  // useState(() => formatValue(value))[0] — le flash ne se déclenchait plus
  // après le 2e changement. On utilise maintenant un useRef mis à jour à chaque
  // render pour comparer à la valeur précédente.
  const prevRef = useRef<string>('');
  const currentFormatted = typeof value === 'number'
    ? (format === 'usd' ? formatUSD(value) : format === 'pct' ? formatPct(value) : formatNumber(value))
    : String(value);

  useEffect(() => {
    if (!flashOnChange) return;
    if (prevRef.current && currentFormatted !== prevRef.current) {
      const prevNum = Number(prevRef.current.replace(/[^0-9.-]/g, ''));
      const currNum = Number(currentFormatted.replace(/[^0-9.-]/g, ''));
      const isUp = currNum > prevNum;
      setFlashClass(isUp ? 'flash-profit' : 'flash-loss');
      const t = setTimeout(() => setFlashClass(''), 800);
      return () => clearTimeout(t);
    }
    prevRef.current = currentFormatted;
  }, [currentFormatted, flashOnChange]);

  const displayValue = typeof value === 'number'
    ? (format === 'usd' ? formatUSD(value) : format === 'pct' ? formatPct(value) : formatNumber(value))
    : value;

  const trendColor = trend === 'up' ? 'text-emerald-400' : trend === 'down' ? 'text-red-400' : 'text-muted';

  return (
    <Card className={cn('relative overflow-hidden group hover:border-border-hi transition-all', flashClass)}>
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-dim mb-2">
            {label}
          </div>
          <div className="font-mono text-2xl font-semibold tracking-tight ticker-num">
            {displayValue}
          </div>
          {(sublabel || trendValue) && (
            <div className="flex items-center gap-2 mt-2">
              {trend && (
                <div className={cn('flex items-center gap-1 text-xs font-medium', trendColor)}>
                  {trend === 'up' ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                  {trendValue}
                </div>
              )}
              {sublabel && (
                <div className="text-xs text-muted">{sublabel}</div>
              )}
            </div>
          )}
        </div>
        {Icon && (
          <div className="ml-3 p-2 rounded-lg bg-card-hover group-hover:bg-primary-500/10 group-hover:text-primary-400 transition-colors">
            <Icon className="w-5 h-5" />
          </div>
        )}
      </div>
    </Card>
  );
}

// Variantes pré-configurées
export function CapitalCard({ value }: { value: number }) {
  return <KPICard label="Capital" value={value} format="usd" icon={Wallet} sublabel="Total balance" />;
}

export function PnLCard({ value, pct }: { value: number; pct: number }) {
  return (
    <KPICard
      label="PnL Total"
      value={pct}
      format="pct"
      icon={pct >= 0 ? TrendingUp : TrendingDown}
      trend={pct >= 0 ? 'up' : 'down'}
      trendValue={formatUSD(value, { sign: true })}
      sublabel="vs initial"
    />
  );
}

export function WinRateCard({ value, totalTrades }: { value: number; totalTrades: number }) {
  return (
    <KPICard
      label="Win Rate"
      value={`${value.toFixed(1)}%`}
      icon={Trophy}
      sublabel={`${totalTrades} trades`}
      trend={value >= 50 ? 'up' : 'down'}
    />
  );
}

export function ProfitFactorCard({ value }: { value: number }) {
  return (
    <KPICard
      label="Profit Factor"
      value={value === 999 ? '∞' : value.toFixed(2)}
      icon={Activity}
      sublabel={value >= 1.5 ? 'Excellent' : value >= 1 ? 'Acceptable' : 'À améliorer'}
      trend={value >= 1.5 ? 'up' : value >= 1 ? 'neutral' : 'down'}
    />
  );
}

export function DrawdownCard({ value, limit }: { value: number; limit: number }) {
  const pct = (Math.abs(value) / limit) * 100;
  const trend = pct > 80 ? 'down' : pct > 50 ? 'neutral' : 'up';
  return (
    <KPICard
      label="Drawdown Global"
      value={`${value.toFixed(2)}%`}
      icon={Zap}
      sublabel={`Limite: -${(limit * 100).toFixed(0)}%`}
      trend={trend}
      trendValue={`${pct.toFixed(0)}% used`}
    />
  );
}
