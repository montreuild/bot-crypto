'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  cn, formatUSD, formatPct, parseSlotKey, lifecycleStyle, timeAgo, formatDateTime,
} from '@/lib/utils';
import { toast } from 'sonner';
import { usePortfolio, useNotifications, useBots } from '@/hooks/use-api';
import { QueryBoundary } from '@/components/ui/query-state';
import { api } from '@/lib/api';
import { useQueryClient } from '@tanstack/react-query';
import {
  Loader2, AlertCircle, Wallet, TrendingUp, TrendingDown, Activity,
  Scale, Bell, ShieldAlert, Zap, RefreshCw, Bot as BotIcon,
} from 'lucide-react';

// ── KPI Card ────────────────────────────────────────────────────────────────

function Kpi({
  label,
  value,
  sub,
  color = 'text-foreground',
  icon,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  icon?: React.ReactNode;
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center justify-between mb-1">
          <div className="text-[10px] uppercase tracking-wider text-dim">{label}</div>
          {icon}
        </div>
        <div className={cn('font-mono text-xl font-bold', color)}>{value}</div>
        {sub && <div className="text-xs text-muted mt-0.5">{sub}</div>}
      </CardContent>
    </Card>
  );
}

// ── Lifecycle counts ────────────────────────────────────────────────────────

function LifecycleCounts({ counts }: { counts: Record<string, number> }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {(['candidat', 'essai', 'actif', 'retire'] as const).map((state) => {
        const style = lifecycleStyle(state);
        const count = counts?.[state] || 0;
        return (
          <div
            key={state}
            className={cn(
              'p-4 rounded-xl border bg-card',
              style.bg,
              'border-border',
            )}
          >
            <div className="text-xs uppercase tracking-wider text-dim">{style.label}</div>
            <div className={cn('text-2xl font-bold mt-1', style.text)}>{count}</div>
          </div>
        );
      })}
    </div>
  );
}

// ── Activity feed ───────────────────────────────────────────────────────────

const LEVEL_VARIANT: Record<string, 'info' | 'warning' | 'danger' | 'default'> = {
  info: 'info',
  warning: 'warning',
  critical: 'danger',
};

const LEVEL_COLOR: Record<string, string> = {
  info: 'text-cyan-400',
  warning: 'text-amber-400',
  critical: 'text-red-400',
};

function ActivityFeed({ items }: { items: any[] }) {
  if (!items || items.length === 0) {
    return <div className="text-sm text-muted text-center py-6">Aucune activité récente</div>;
  }
  return (
    <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
      {items.slice(0, 30).map((n, i) => {
        const level = n.level || 'info';
        return (
          <div
            key={i}
            className="flex items-start gap-3 p-3 rounded-lg bg-card-hover border border-border"
          >
            <div className={cn('mt-0.5', LEVEL_COLOR[level] || 'text-muted')}>
              {level === 'critical' ? (
                <ShieldAlert className="w-4 h-4" />
              ) : level === 'warning' ? (
                <AlertCircle className="w-4 h-4" />
              ) : (
                <Bell className="w-4 h-4" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold truncate">
                  {n.title || n.message?.slice(0, 60) || 'Notification'}
                </span>
                <Badge variant={LEVEL_VARIANT[level] || 'default'}>{level}</Badge>
              </div>
              {n.message && (
                <div className="text-xs text-muted mt-0.5 break-words">{n.message}</div>
              )}
              <div className="text-[10px] text-dim font-mono mt-1">
                {n.ts ? timeAgo(n.ts) : ''}
                {n.ts && <span className="ml-2">· {formatDateTime(n.ts)}</span>}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function PortfolioPage() {
  const { data: portfolio, isLoading, isError, error, refetch, isRefetching } = usePortfolio();
  const { data: notifData } = useNotifications(30, 'info');
  const { data: botsData } = useBots();
  const qc = useQueryClient();

  const p = portfolio || {};
  const capital = p.capital ?? 0;
  const totalPnl = p.total_pnl ?? 0;
  const totalPnlPct = p.total_pnl_pct ?? 0;
  const winRate = p.win_rate ?? 0;
  const openPositions = p.open_positions ?? p.positions?.length ?? 0;

  // Real vs shadow allocation
  const realSlots: any[] = p.real_allocation || p.slots || p.allocation || [];
  const shadowSlots: any[] = p.shadow_allocation?.slots || p.shadow_allocation || [];

  // Lifecycle
  const lifecycle = p.lifecycle || {};
  const lifecycleCounts: Record<string, number> = lifecycle.counts || p.lifecycle_counts || {};
  const bots = botsData?.bots || p.bots || [];
  const significantBots = bots.filter((b: any) => b?.edge_significant);

  // Risk
  const risk = p.risk || p;
  const halted = risk.halted ?? risk.circuit_breaker_active ?? false;
  const killSwitch = risk.kill_switch ?? false;
  const globalDdPct = risk.global_dd_pct ?? 0;
  const dailyPnlPct = risk.daily_pnl_pct ?? 0;
  const continuousAllocation = p.continuous_allocation ?? true;

  const handleForceRebalance = async () => {
    try {
      toast.info('Rebalance en cours...');
      await api.forceRebalance();
      toast.success('Rebalance déclenché');
      qc.invalidateQueries({ queryKey: ['portfolio'] });
      qc.invalidateQueries({ queryKey: ['bots'] });
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  // S6-12 : titre monté en permanence + cause réelle de l'erreur et réessai,
  // au lieu d'un « Erreur lors du chargement » sans détail ni recours.
  if (isError || isLoading) {
    return (
      <QueryBoundary
        title={
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Portefeuille</h1>
            <p className="text-sm text-muted mt-1">
              Vue détaillée fonds · allocation réelle vs shadow · lifecycle · risk
            </p>
          </div>
        }
        error={isError ? error : undefined}
        isLoading={isLoading}
        loadingLabel="Chargement du portefeuille…"
        onRetry={() => refetch()}
        isRetrying={isRefetching}
      >
        {null}
      </QueryBoundary>
    );
  }

  const pnlColor = totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400';

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Portefeuille</h1>
          <p className="text-sm text-muted mt-1">
            Vue détaillée fonds · allocation réelle vs shadow · lifecycle · risk
          </p>
        </div>
        {!continuousAllocation && (
          <Button onClick={handleForceRebalance} variant="primary" size="sm">
            <RefreshCw className="w-3.5 h-3.5" />
            Force rebalance
          </Button>
        )}
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
        <Kpi
          label="Capital"
          value={formatUSD(capital)}
          icon={<Wallet className="w-3.5 h-3.5 text-dim" />}
        />
        <Kpi
          label="PnL total"
          value={formatUSD(totalPnl)}
          sub={formatPct(totalPnlPct)}
          color={pnlColor}
          icon={totalPnl >= 0 ? <TrendingUp className="w-3.5 h-3.5 text-emerald-400" /> : <TrendingDown className="w-3.5 h-3.5 text-red-400" />}
        />
        <Kpi
          label="Win rate"
          value={`${winRate.toFixed(1)}%`}
          color={winRate >= 50 ? 'text-emerald-400' : 'text-red-400'}
          icon={<Activity className="w-3.5 h-3.5 text-dim" />}
        />
        <Kpi
          label="Positions ouvertes"
          value={String(openPositions)}
          icon={<Scale className="w-3.5 h-3.5 text-dim" />}
        />
        <Kpi
          label="Daily PnL %"
          value={formatPct(dailyPnlPct)}
          color={dailyPnlPct >= 0 ? 'text-emerald-400' : 'text-red-400'}
        />
      </div>

      {/* Risk panel */}
      <Card>
        <CardHeader>
          <CardTitle>Risk</CardTitle>
          <ShieldAlert className={cn('w-4 h-4', halted ? 'text-red-400 animate-pulse' : 'text-emerald-400')} />
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <RiskIndicator label="Halted" active={asBool(halted)} variant="danger" />
            <RiskIndicator label="Kill switch" active={asBool(killSwitch)} variant="danger" />
            <div>
              <div className="text-xs text-dim mb-1">Global DD</div>
              <div className={cn('font-mono text-lg font-semibold', globalDdPct >= 15 ? 'text-red-400' : 'text-amber-400')}>
                {globalDdPct.toFixed(2)}%
              </div>
              <div className="h-1 bg-card-hover rounded-full overflow-hidden mt-1">
                <div
                  className={cn('h-full', globalDdPct >= 15 ? 'bg-red-400' : 'bg-amber-400')}
                  style={{ width: `${Math.min(globalDdPct, 100)}%` }}
                />
              </div>
            </div>
            <div>
              <div className="text-xs text-dim mb-1">Daily PnL</div>
              <div className={cn('font-mono text-lg font-semibold', dailyPnlPct >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                {formatPct(dailyPnlPct)}
              </div>
              <div className="text-[10px] text-dim mt-1">vs limite daily_dd_limit</div>
            </div>
          </div>
          {!continuousAllocation && (
            <div className="mt-4 text-xs text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 flex items-center gap-2">
              <Zap className="w-3.5 h-3.5" />
              continuous_allocation = false — utilisez le bouton "Force rebalance" en haut de page.
            </div>
          )}
        </CardContent>
      </Card>

      {/* Real vs Shadow allocation */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Allocation réelle</CardTitle>
            <Badge variant="success">{realSlots.length} slots</Badge>
          </CardHeader>
          <CardContent className="p-0">
            <AllocationTable slots={realSlots} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Allocation shadow</CardTitle>
            <Badge variant="info">{shadowSlots.length} slots</Badge>
          </CardHeader>
          <CardContent className="p-0">
            <AllocationTable slots={shadowSlots} />
          </CardContent>
        </Card>
      </div>

      {/* Lifecycle */}
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted mb-3">
          Lifecycle
        </h2>
        <LifecycleCounts counts={lifecycleCounts} />
      </div>

      {/* Bots with edge_significant */}
      <Card>
        <CardHeader>
          <CardTitle>Bots avec edge significatif</CardTitle>
          <Badge variant="purple">{significantBots.length}</Badge>
        </CardHeader>
        <CardContent className="p-0">
          {significantBots.length === 0 ? (
            <div className="text-sm text-muted text-center py-6">
              Aucun bot avec edge significatif actuellement
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-dim border-b border-border">
                    <th className="p-3 font-medium">Slot</th>
                    <th className="p-3 font-medium">État</th>
                    <th className="p-3 font-medium text-right">Budget %</th>
                    <th className="p-3 font-medium text-right">Used %</th>
                    <th className="p-3 font-medium text-right">Edge CI Low</th>
                    <th className="p-3 font-medium text-right">Trades</th>
                    <th className="p-3 font-medium text-right">Weekly PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {significantBots.map((bot: any) => {
                    const { strategy, tf, symbol } = parseSlotKey(bot.slot_key);
                    const style = lifecycleStyle(bot.state);
                    const edge = bot.edge || {};
                    const budget = bot.budget || {};
                    return (
                      <tr key={bot.slot_key} className="border-b border-border/30 hover:bg-card-hover">
                        <td className="p-3">
                          <div className="flex items-center gap-2">
                            <BotIcon className="w-3.5 h-3.5 text-primary-400" />
                            <div>
                              <div className="font-mono font-semibold text-xs">{strategy}</div>
                              <div className="text-[10px] text-dim">{tf} · {symbol || '—'}</div>
                            </div>
                          </div>
                        </td>
                        <td className="p-3">
                          <Badge variant={
                            bot.state === 'actif' ? 'success' :
                            bot.state === 'essai' ? 'info' :
                            bot.state === 'retire' ? 'danger' : 'warning'
                          }>
                            <span className={style.text}>{style.icon}</span>
                            {style.label}
                          </Badge>
                        </td>
                        <td className="p-3 text-right font-mono">{(budget.budget_pct ?? 0).toFixed(1)}%</td>
                        <td className="p-3 text-right font-mono text-muted">{(budget.used_pct ?? 0).toFixed(1)}%</td>
                        <td className={cn('p-3 text-right font-mono font-semibold', (edge.ci_low_pct ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400')}>
                          {(edge.ci_low_pct ?? 0).toFixed(2)}%
                        </td>
                        <td className="p-3 text-right font-mono text-muted">{edge.n ?? 0}</td>
                        <td className={cn('p-3 text-right font-mono', (budget.weekly_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                          {(budget.weekly_pnl ?? 0) >= 0 ? '+' : ''}{formatUSD(budget.weekly_pnl ?? 0)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Activity feed */}
      <Card>
        <CardHeader>
          <CardTitle>Activité récente</CardTitle>
          <Bell className="w-4 h-4 text-primary-400" />
        </CardHeader>
        <CardContent>
          <ActivityFeed items={notifData?.notifications || p.activity || []} />
        </CardContent>
      </Card>
    </div>
  );
}

// Helper: coerce any value to a strict boolean (handles 0/1/"true"/"false")
function asBool(v: any): boolean {
  if (typeof v === 'boolean') return v;
  if (v == null) return false;
  if (typeof v === 'number') return v !== 0;
  if (typeof v === 'string') return v.toLowerCase() === 'true' || v === '1';
  return Boolean(v);
}

function RiskIndicator({
  label,
  active,
  variant,
}: {
  label: string;
  active: boolean;
  variant: 'danger' | 'warning' | 'success';
}) {
  const color =
    active && variant === 'danger'
      ? 'text-red-400 border-red-500/40 bg-red-500/10'
      : variant === 'warning' && active
        ? 'text-amber-400 border-amber-500/40 bg-amber-500/10'
        : 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10';
  return (
    <div>
      <div className="text-xs text-dim mb-1">{label}</div>
      <div className={cn('inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border font-mono text-sm font-semibold', color)}>
        <span className={cn('w-1.5 h-1.5 rounded-full', active ? 'bg-current animate-pulse' : 'bg-current opacity-40')} />
        {active ? 'ACTIF' : 'OK'}
      </div>
    </div>
  );
}

// ── Allocation table ────────────────────────────────────────────────────────

function AllocationTable({ slots }: { slots: any[] }) {
  if (!slots || slots.length === 0) {
    return <div className="text-sm text-muted text-center py-6">Aucun slot</div>;
  }
  return (
    <div className="overflow-x-auto max-h-80">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-card">
          <tr className="text-left text-dim border-b border-border">
            <th className="p-2 font-medium">Slot</th>
            <th className="p-2 font-medium text-right">Budget %</th>
            <th className="p-2 font-medium text-right">Used %</th>
            <th className="p-2 font-medium text-right">Weekly PnL</th>
            <th className="p-2 font-medium text-right">Weekly Trades</th>
          </tr>
        </thead>
        <tbody>
          {slots.map((s: any, i: number) => {
            const key = s.slot_key || `slot-${i}`;
            const { strategy, tf } = parseSlotKey(key);
            const weeklyPnl = s.weekly_pnl ?? 0;
            return (
              <tr key={key} className="border-b border-border/30 hover:bg-card-hover">
                <td className="p-2">
                  <div className="font-mono font-semibold">{strategy}</div>
                  <div className="text-[10px] text-dim">{tf}{s.symbol ? ` · ${s.symbol}` : ''}</div>
                </td>
                <td className="p-2 text-right font-mono">{(s.budget_pct ?? 0).toFixed(1)}%</td>
                <td className="p-2 text-right font-mono text-muted">{(s.used_pct ?? 0).toFixed(1)}%</td>
                <td className={cn('p-2 text-right font-mono', weeklyPnl >= 0 ? 'text-emerald-400' : 'text-red-400')}>
                  {weeklyPnl >= 0 ? '+' : ''}{formatUSD(weeklyPnl)}
                </td>
                <td className="p-2 text-right font-mono text-muted">{s.weekly_trades ?? 0}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
