'use client';

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { useBotStatus } from '@/hooks/use-api';
import { cn } from '@/lib/utils';
import { Shield, AlertTriangle, Activity } from 'lucide-react';

export function RiskPanel() {
  const { data: status } = useBotStatus();

  const dailyDdPct = status?.daily_pnl_pct || 0;
  const dailyDdLimit = status?.daily_dd_limit || 0.05;
  const globalDdPct = status?.global_dd_pct || 0;
  const globalDdLimit = status?.global_dd_limit || 0.20;
  const cbActive = status?.circuit_breaker_active || false;
  const volatilityBrake = status?.volatility_brake || false;

  const dailyPct = Math.min(Math.abs(dailyDdPct) / (dailyDdLimit * 100) * 100, 100);
  const globalPct = Math.min(Math.abs(globalDdPct) / (globalDdLimit * 100) * 100, 100);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Shield className="w-3.5 h-3.5" />
          Risk Management
        </CardTitle>
        {(cbActive || volatilityBrake) && (
          <span className="px-2 py-0.5 rounded-full bg-red-500/10 text-red-400 text-[10px] font-semibold animate-pulse">
            ALERT
          </span>
        )}
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Daily DD */}
        <div>
          <div className="flex justify-between text-xs mb-2">
            <span className="text-muted">Drawdown Journalier</span>
            <span className={cn('font-mono', dailyPct > 80 ? 'text-red-400' : dailyPct > 50 ? 'text-amber-400' : 'text-emerald-400')}>
              {Math.abs(dailyDdPct).toFixed(2)}% / {(dailyDdLimit * 100).toFixed(0)}%
            </span>
          </div>
          <div className="h-2 bg-card-hover rounded-full overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-all duration-500',
                dailyPct > 80 ? 'bg-red-400' : dailyPct > 50 ? 'bg-amber-400' : 'bg-emerald-400',
              )}
              style={{ width: `${dailyPct}%` }}
            />
          </div>
        </div>

        {/* Global DD */}
        <div>
          <div className="flex justify-between text-xs mb-2">
            <span className="text-muted">Drawdown Global</span>
            <span className={cn('font-mono', globalPct > 80 ? 'text-red-400' : globalPct > 50 ? 'text-amber-400' : 'text-emerald-400')}>
              {Math.abs(globalDdPct).toFixed(2)}% / {(globalDdLimit * 100).toFixed(0)}%
            </span>
          </div>
          <div className="h-2 bg-card-hover rounded-full overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-all duration-500',
                globalPct > 80 ? 'bg-red-400' : globalPct > 50 ? 'bg-amber-400' : 'bg-emerald-400',
              )}
              style={{ width: `${globalPct}%` }}
            />
          </div>
        </div>

        {/* Status badges */}
        <div className="flex flex-wrap gap-2 pt-2 border-t border-border">
          {cbActive ? (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-red-500/10 text-red-400 text-xs">
              <AlertTriangle className="w-3 h-3" />
              Circuit Breaker Actif
            </div>
          ) : (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-emerald-500/10 text-emerald-400 text-xs">
              <Activity className="w-3 h-3" />
              CB OK
            </div>
          )}

          {volatilityBrake && (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-amber-500/10 text-amber-400 text-xs">
              <AlertTriangle className="w-3 h-3" />
              Volatility Brake
            </div>
          )}

          {/*
            `current_risk` n'existe que si le moteur de risque tourne : c'est
            le taux du profil MODULÉ par la courbe de drawdown. Le repli
            historique `|| 1` affichait « 1.00% » bot arrêté — un chiffre
            inventé présenté comme un fait, et faux depuis S12 où le profil
            par défaut vaut 2,5 %. Une absence se dit, elle ne se comble pas.
          */}
          <div
            className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-card-hover text-muted text-xs"
            title={
              status?.current_risk != null
                ? 'Risque par trade, en % de l’enveloppe du slot (taux du profil × courbe de drawdown)'
                : 'Disponible quand le trader tourne — le taux courant dépend du drawdown en cours'
            }
          >
            Risk: {status?.current_risk != null
              ? `${status.current_risk.toFixed(2)}%`
              : <span className="text-dim">—</span>}
          </div>
        </div>

        {cbActive && status?.circuit_breaker_reason && (
          <div className="text-xs text-red-400 p-2 rounded bg-red-500/5 border border-red-500/20">
            {status.circuit_breaker_reason}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
