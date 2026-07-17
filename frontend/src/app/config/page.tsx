'use client';

import { useConfig, usePresets, useSetRiskPreset } from '@/hooks/use-api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { useState } from 'react';
import { Save, RotateCcw, Shield } from 'lucide-react';

export default function ConfigPage() {
  const { data: config, isLoading } = useConfig();
  const { data: presets } = usePresets();
  const setPreset = useSetRiskPreset();

  const [activeTab, setActiveTab] = useState<'strategies' | 'risk' | 'notifications' | 'exchange'>('strategies');

  if (isLoading || !config) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="w-12 h-12 rounded-full border-2 border-primary-400 border-t-transparent animate-spin" />
      </div>
    );
  }

  const handlePreset = async (preset: string) => {
    try {
      await setPreset.mutateAsync(preset);
      toast.success(`Preset "${preset}" appliqué`);
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const tabs = [
    { id: 'strategies' as const, label: 'Stratégies', icon: Shield },
    { id: 'risk' as const, label: 'Risk', icon: Shield },
    { id: 'notifications' as const, label: 'Notifications', icon: Shield },
    { id: 'exchange' as const, label: 'Exchange', icon: Shield },
  ];

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Configuration</h1>
        <p className="text-sm text-muted mt-1">
          Gérez vos stratégies, paramètres de risque et notifications
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                'flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-all',
                activeTab === tab.id
                  ? 'border-primary-400 text-primary-400'
                  : 'border-transparent text-muted hover:text-foreground',
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {activeTab === 'strategies' && (
        <Card>
          <CardHeader>
            <CardTitle>Stratégies Activées</CardTitle>
            <Badge variant="info">{config.strategies?.enabled?.length || 0}</Badge>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {(config.strategies?.enabled || []).map((s: string) => (
                <div key={s} className="flex items-center justify-between p-3 rounded-lg bg-card-hover border border-border">
                  <span className="font-mono text-sm">{s}</span>
                  <span className="w-2 h-2 rounded-full bg-emerald-400" />
                </div>
              ))}
            </div>
            <div className="mt-6">
              <CardTitle className="mb-3">Timeframes</CardTitle>
              <div className="flex flex-wrap gap-2">
                {(config.trading?.timeframes || []).map((tf: string) => (
                  <Badge key={tf} variant="purple">{tf}</Badge>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {activeTab === 'risk' && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {(Object.entries(presets?.presets || {}) as [string, any][]).map(([key, p]) => (
              <Card
                key={key}
                className={cn(
                  'cursor-pointer hover:border-primary-400 transition-all',
                  presets?.current === key && 'border-primary-400 ring-2 ring-primary-400/20',
                )}
                onClick={() => handlePreset(key)}
              >
                <CardContent>
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="font-semibold">{p.label}</h3>
                    {presets?.current === key && <Badge variant="success">Actuel</Badge>}
                  </div>
                  <div className="space-y-1 text-xs text-muted">
                    <div className="flex justify-between"><span>Risk/trade</span><span className="font-mono">{(p.risk_per_trade * 100).toFixed(1)}%</span></div>
                    <div className="flex justify-between"><span>Max positions</span><span className="font-mono">{p.max_positions}</span></div>
                    <div className="flex justify-between"><span>Daily DD</span><span className="font-mono">{(p.daily_drawdown_limit * 100).toFixed(1)}%</span></div>
                    <div className="flex justify-between"><span>Global DD</span><span className="font-mono">{(p.max_drawdown_global * 100).toFixed(0)}%</span></div>
                    <div className="flex justify-between"><span>Kill switch</span><span className="font-mono">{(p.equity_kill_switch_dd * 100).toFixed(0)}%</span></div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          <Card>
            <CardHeader><CardTitle>Paramètres actuels</CardTitle></CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <div className="text-xs text-dim">Capital</div>
                  <div className="font-mono font-semibold">${config.trading?.capital}</div>
                </div>
                <div>
                  <div className="text-xs text-dim">Risk per trade</div>
                  <div className="font-mono font-semibold">{((config.trading?.risk_per_trade || 0) * 100).toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-xs text-dim">Max positions</div>
                  <div className="font-mono font-semibold">{config.trading?.max_positions}</div>
                </div>
                <div>
                  <div className="text-xs text-dim">Score threshold</div>
                  <div className="font-mono font-semibold">{config.trading?.score_threshold}</div>
                </div>
                <div>
                  <div className="text-xs text-dim">Daily DD limit</div>
                  <div className="font-mono font-semibold">{((config.trading?.daily_drawdown_limit || 0) * 100).toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-xs text-dim">Global DD limit</div>
                  <div className="font-mono font-semibold">{((config.trading?.max_drawdown_global || 0) * 100).toFixed(0)}%</div>
                </div>
                <div>
                  <div className="text-xs text-dim">Taker fee</div>
                  <div className="font-mono font-semibold">{((config.trading?.taker_fee || 0) * 100).toFixed(2)}%</div>
                </div>
                <div>
                  <div className="text-xs text-dim">Maker fee</div>
                  <div className="font-mono font-semibold">{((config.trading?.maker_fee || 0) * 100).toFixed(2)}%</div>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {activeTab === 'notifications' && (
        <Card>
          <CardHeader><CardTitle>Notifications</CardTitle></CardHeader>
          <CardContent>
            <div className="space-y-3">
              <NotifRow label="Telegram" enabled={config.notifications?.telegram_enabled} />
              <NotifRow label="WhatsApp" enabled={config.notifications?.whatsapp_enabled} />
              <NotifRow label="Email" enabled={config.notifications?.email_enabled} />
            </div>
          </CardContent>
        </Card>
      )}

      {activeTab === 'exchange' && (
        <Card>
          <CardHeader><CardTitle>Exchange</CardTitle></CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm">
              <div>
                <div className="text-xs text-dim">Name</div>
                <div className="font-mono font-semibold">{config.exchange?.name}</div>
              </div>
              <div>
                <div className="text-xs text-dim">Margin</div>
                <div className="font-mono font-semibold">{config.exchange?.margin ? 'Enabled' : 'Disabled'}</div>
              </div>
              <div>
                <div className="text-xs text-dim">API Key</div>
                <div className="font-mono font-semibold">
                  {config.exchange?.api_key ? '••••••••' : '—'}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function NotifRow({ label, enabled }: { label: string; enabled?: boolean }) {
  return (
    <div className="flex items-center justify-between p-3 rounded-lg bg-card-hover border border-border">
      <span className="text-sm font-medium">{label}</span>
      <Badge variant={enabled ? 'success' : 'default'}>
        {enabled ? 'Activé' : 'Désactivé'}
      </Badge>
    </div>
  );
}
