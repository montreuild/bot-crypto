'use client';

import { useBotStatus, useHealth } from '@/hooks/use-api';
import { useWebSocket } from '@/lib/ws-provider';
import { cn, formatUSD } from '@/lib/utils';
import {
  Play, Square, RefreshCw, AlertTriangle, Wifi, WifiOff, Loader2,
} from 'lucide-react';
import { useStartBot, useStopBot, useResetHalt } from '@/hooks/use-api';
import { toast } from 'sonner';
import { useMemo } from 'react';

export function Topbar() {
  const { data: status } = useBotStatus();
  const { data: health } = useHealth();
  const { status: wsStatus } = useWebSocket();
  const startBot = useStartBot();
  const stopBot = useStopBot();
  const resetHalt = useResetHalt();

  const isRunning = status?.status === 'running';
  const isPaperMode = status?.paper_mode ?? true;
  const pnlPct = status?.total_pnl_pct ?? 0;
  const cbActive = status?.circuit_breaker_active ?? false;

  const onToggleBot = async () => {
    try {
      if (isRunning) {
        await stopBot.mutateAsync(false);
        toast.success('Bot arrêté');
      } else {
        await startBot.mutateAsync();
        toast.success('Bot démarré');
      }
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const onResetHalt = async () => {
    try {
      await resetHalt.mutateAsync(false);
      toast.success('Circuit breaker réinitialisé');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  // PnL color
  const pnlColorClass = useMemo(() => {
    if (pnlPct > 0) return 'text-emerald-400';
    if (pnlPct < 0) return 'text-red-400';
    return 'text-muted';
  }, [pnlPct]);

  return (
    <header className="h-16 bg-surface border-b border-border flex items-center px-6 gap-4 flex-shrink-0">
      {/* Bot control */}
      <button
        onClick={onToggleBot}
        disabled={startBot.isPending || stopBot.isPending}
        className={cn(
          'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all',
          isRunning
            ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/30'
            : 'bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 border border-emerald-500/30',
          (startBot.isPending || stopBot.isPending) && 'opacity-50 cursor-not-allowed',
        )}
      >
        {(startBot.isPending || stopBot.isPending) ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : isRunning ? (
          <Square className="w-4 h-4" fill="currentColor" />
        ) : (
          <Play className="w-4 h-4" fill="currentColor" />
        )}
        {isRunning ? 'Arrêter' : 'Démarrer'}
      </button>

      {/* Mode badge */}
      <div className={cn(
        'px-3 py-1.5 rounded-full text-xs font-semibold border',
        isPaperMode
          ? 'bg-amber-500/10 text-amber-400 border-amber-500/30'
          : 'bg-red-500/10 text-red-400 border-red-500/30 animate-pulse',
      )}>
        {isPaperMode ? 'PAPER' : 'LIVE'}
      </div>

      {/* Status indicator */}
      <div className="flex items-center gap-2 text-sm">
        <span className={cn(
          'w-2 h-2 rounded-full',
          isRunning ? 'bg-emerald-400 animate-pulse' : 'bg-dim',
        )} />
        <span className="text-muted">{isRunning ? 'Running' : 'Stopped'}</span>
        {status?.cycle !== undefined && (
          <span className="text-dim font-mono text-xs">· cycle #{status.cycle}</span>
        )}
      </div>

      {/* Circuit breaker */}
      {cbActive && (
        <button
          onClick={onResetHalt}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-red-500/10 text-red-400 border border-red-500/30 text-xs font-semibold animate-pulse hover:bg-red-500/20"
        >
          <AlertTriangle className="w-3.5 h-3.5" />
          CB Active — Reset
        </button>
      )}

      <div className="flex-1" />

      {/* Capital & PnL */}
      {status?.capital !== undefined && (
        <div className="flex items-center gap-6">
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-wider text-dim">Capital</div>
            <div className="font-mono font-semibold text-sm">{formatUSD(status.capital)}</div>
          </div>
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-wider text-dim">PnL Total</div>
            <div className={cn('font-mono font-semibold text-sm', pnlColorClass)}>
              {pnlPct > 0 ? '+' : ''}{pnlPct.toFixed(2)}%
            </div>
          </div>
        </div>
      )}

      {/* WS status */}
      <div className="flex items-center gap-2 ml-2">
        {wsStatus === 'connected' ? (
          <Wifi className="w-4 h-4 text-emerald-400" />
        ) : wsStatus === 'connecting' ? (
          <Loader2 className="w-4 h-4 text-amber-400 animate-spin" />
        ) : (
          <WifiOff className="w-4 h-4 text-red-400" />
        )}
      </div>

      {/* Health */}
      {health && (
        <div className="flex items-center gap-1 text-xs">
          {health.db && <span title="DB OK" className="w-2 h-2 rounded-full bg-emerald-400" />}
          {health.exchange && <span title="Exchange OK" className="w-2 h-2 rounded-full bg-emerald-400" />}
          {health.trader && <span title="Trader OK" className="w-2 h-2 rounded-full bg-emerald-400" />}
        </div>
      )}
    </header>
  );
}
