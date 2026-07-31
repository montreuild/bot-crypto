'use client';

// S5-01 / UI-02 fix : page Config multi-symbole.
// L'audit V2 notait que config.html (Jinja2) restait mono-symbole malgré le
// moteur per-symbole. Cette version Next.js ajoute un sélecteur de symbole
// et permet de sauvegarder des overrides par symbole pour les paramètres
// de stratégie et l'activation par timeframe.

import { useConfig, usePresets, useSetRiskPreset, useSetStrategyParams, useToggleStrategyTimeframe, useApiStatus } from '@/hooks/use-api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { useState, useMemo } from 'react';
import { Save, RotateCcw, Shield, Settings, Activity, DollarSign, Globe } from 'lucide-react';
import { QueryBoundary } from '@/components/ui/query-state';

type Symbol = string; // ex. "BTC/USDC"

export default function ConfigPage() {
  const configQuery = useConfig();
  const { data: config } = configQuery;
  const { data: presets } = usePresets();
  const { data: status } = useApiStatus();
  const setPreset = useSetRiskPreset();
  const setStrategyParams = useSetStrategyParams();
  const toggleTf = useToggleStrategyTimeframe();

  const [activeTab, setActiveTab] = useState<'strategies' | 'risk' | 'notifications' | 'exchange'>('strategies');
  const [selectedSymbol, setSelectedSymbol] = useState<Symbol | 'all'>('all');
  const [editingParams, setEditingParams] = useState<Record<string, Record<string, any>>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);

  // Liste des symboles disponibles depuis la config + trades récents + last_scan.
  // On évite d'utiliser `status.scanner` qui n'existe pas sur BotStatus —
  // à la place on combine les symboles de la config + last_symbols_scanned
  // (présents sur BotStatus quand le bot tourne) + un fallback par défaut.
  const availableSymbols: Symbol[] = useMemo(() => {
    const fromConfig: Symbol[] = config?.scanner?.symbols || [];
    const fromStatus: Symbol[] = status?.last_symbols_scanned || [];
    const all = new Set<string>([...fromConfig, ...fromStatus]);
    if (all.size === 0) return ['BTC/USDC', 'ETH/USDC', 'XRP/USDC'];
    return Array.from(all).sort();
  }, [status, config]);

  // S6-12 : le titre reste monté même sans config chargée, et l'échec est
  // affiché/réessayable au lieu d'un spinner qui ne s'arrête jamais.
  if (!config) {
    return (
      <QueryBoundary
        title={
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Configuration</h1>
            <p className="text-sm text-muted mt-1">
              Gérez vos stratégies, paramètres de risque et notifications
            </p>
          </div>
        }
        query={configQuery}
        loadingLabel="Chargement de la configuration…"
        onRetry={() => configQuery.refetch()}
      >
        {null}
      </QueryBoundary>
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

  const handleParamChange = (strategyName: string, paramName: string, value: any) => {
    setEditingParams((prev) => ({
      ...prev,
      [strategyName]: { ...(prev[strategyName] || {}), [paramName]: value },
    }));
  };

  const handleSaveParams = async (strategyName: string) => {
    const params = editingParams[strategyName];
    if (!params) return;
    setSavingKey(strategyName);
    try {
      const symbol = selectedSymbol === 'all' ? undefined : selectedSymbol;
      await setStrategyParams.mutateAsync({
        strategy: strategyName,
        params,
        symbol, // S5-01 : permet d'écrire un override par symbole
      });
      toast.success(
        `Paramètres de ${strategyName} sauvegardés${symbol ? ` pour ${symbol}` : ''}`,
      );
      setEditingParams((prev) => {
        const next = { ...prev };
        delete next[strategyName];
        return next;
      });
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    } finally {
      setSavingKey(null);
    }
  };

  const handleToggleTf = async (tf: string, enable: boolean) => {
    try {
      const symbol = selectedSymbol === 'all' ? undefined : selectedSymbol;
      await toggleTf.mutateAsync({ tf, enable, symbol });
      toast.success(
        `Timeframe ${tf} ${enable ? 'activée' : 'désactivée'}${symbol ? ` pour ${symbol}` : ''}`,
      );
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const tabs = [
    { id: 'strategies' as const, label: 'Stratégies', icon: Shield },
    { id: 'risk' as const, label: 'Risk', icon: Activity },
    { id: 'notifications' as const, label: 'Notifications', icon: DollarSign },
    { id: 'exchange' as const, label: 'Exchange', icon: Globe },
  ];

  // Obtenir les params d'une stratégie pour le symbole sélectionné
  const getStrategyParams = (strategyName: string): Record<string, any> => {
    if (selectedSymbol === 'all') {
      return config.strategy_params?.[strategyName] || {};
    }
    // Override par symbole s'il existe, sinon params globaux
    const override = config.strategy_params?.[strategyName]?.symbol_overrides?.[selectedSymbol];
    return override || config.strategy_params?.[strategyName] || {};
  };

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Configuration</h1>
          <p className="text-sm text-muted mt-1">
            Gérez vos stratégies, paramètres de risque et notifications
          </p>
        </div>
        {/* S5-01 : sélecteur de symbole pour overrides par symbole */}
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted uppercase tracking-wider">Symbole</label>
          <select
            aria-label="Symbole"
            value={selectedSymbol}
            onChange={(e) => setSelectedSymbol(e.target.value as Symbol | 'all')}
            className="px-3 py-1.5 bg-card border border-border rounded-lg text-sm focus:outline-none focus:border-primary-400"
          >
            <option value="all">Tous (global)</option>
            {availableSymbols.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          {selectedSymbol !== 'all' && (
            <Badge variant="info">override {selectedSymbol}</Badge>
          )}
        </div>
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
        <div className="space-y-4">
          {/* Stratégies activées avec overrides par symbole */}
          <Card>
            <CardHeader>
              <CardTitle>Stratégies Activées</CardTitle>
              <Badge variant="info">{config.strategies?.enabled?.length || 0}</Badge>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                {(config.strategies?.enabled || []).map((s: string) => {
                  const params = getStrategyParams(s);
                  const hasOverride = selectedSymbol !== 'all' && params?.symbol_overrides?.[selectedSymbol];
                  return (
                    <div
                      key={s}
                      className={cn(
                        'p-3 rounded-lg border',
                        hasOverride
                          ? 'border-primary-400 bg-primary-500/5'
                          : 'border-border bg-card-hover',
                      )}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-mono text-sm">{s}</span>
                        <span className="w-2 h-2 rounded-full bg-emerald-400" />
                      </div>
                      {/* Afficher les paramètres éditables */}
                      {Object.keys(params || {}).filter(k => k !== 'symbol_overrides').length > 0 && (
                        <div className="space-y-2 mt-3 pt-3 border-t border-border/50">
                          {Object.entries(params)
                            .filter(([k]) => k !== 'symbol_overrides')
                            .slice(0, 3)
                            .map(([k, v]: [string, any]) => (
                              <div key={k} className="flex items-center gap-2">
                                <label className="text-xs text-muted font-mono w-1/2 truncate">{k}</label>
                                <input
                                  type="text"
                                  value={editingParams[s]?.[k] ?? v}
                                  onChange={(e) => handleParamChange(s, k, e.target.value)}
                                  className="flex-1 px-2 py-1 bg-surface border border-border rounded text-xs font-mono focus:outline-none focus:border-primary-400"
                                />
                              </div>
                            ))}
                        </div>
                      )}
                      {editingParams[s] && (
                        <Button
                          onClick={() => handleSaveParams(s)}
                          disabled={savingKey === s}
                          size="sm"
                          className="w-full mt-3"
                        >
                          <Save className="w-3 h-3 mr-1" />
                          {savingKey === s ? 'Sauvegarde...' : `Sauver${selectedSymbol !== 'all' ? ` (${selectedSymbol})` : ''}`}
                        </Button>
                      )}
                      {hasOverride && (
                        <Badge variant="info" className="mt-2 text-[10px]">
                          Override {selectedSymbol}
                        </Badge>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Timeframes avec activation par symbole */}
              <div className="mt-6">
                <CardTitle className="mb-3 text-sm">Timeframes {selectedSymbol !== 'all' && <Badge variant="info" className="ml-2 text-[10px]">{selectedSymbol}</Badge>}</CardTitle>
                <div className="flex flex-wrap gap-2">
                  {(config.trading?.timeframes || []).map((tf: string) => (
                    <button
                      key={tf}
                      onClick={() => handleToggleTf(tf, true)}
                      className="px-3 py-1.5 rounded-lg border border-primary-400 bg-primary-500/10 text-primary-400 text-sm font-medium hover:bg-primary-500/20"
                    >
                      {tf}
                      <span className="ml-2 text-xs">✓</span>
                    </button>
                  ))}
                </div>
                <p className="text-xs text-muted mt-2">
                  Cliquez pour activer une TF {selectedSymbol !== 'all' && `pour ${selectedSymbol}`}. Les overrides par symbole sont stockés dans <code className="font-mono text-xs">optimizer_results[tf][symbol]</code>.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {activeTab === 'risk' && (
        <Card>
          <CardHeader>
            <CardTitle>Paramètres de Risque</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-muted uppercase tracking-wider">Capital (USDC)</label>
                <div className="text-lg font-mono mt-1">{config.trading?.capital || 1000}</div>
              </div>
              <div>
                <label className="text-xs text-muted uppercase tracking-wider">Risk per trade</label>
                <div className="text-lg font-mono mt-1">{((config.trading?.risk_per_trade || 0.01) * 100).toFixed(1)}%</div>
              </div>
              <div>
                <label className="text-xs text-muted uppercase tracking-wider">Max DD global</label>
                <div className="text-lg font-mono mt-1">{((config.trading?.max_drawdown_global || 0.20) * 100).toFixed(0)}%</div>
              </div>
              <div>
                <label className="text-xs text-muted uppercase tracking-wider">Daily DD limit</label>
                <div className="text-lg font-mono mt-1">{((config.trading?.daily_drawdown_limit || 0.05) * 100).toFixed(0)}%</div>
              </div>
            </div>

            {/* Presets de risque */}
            <div className="pt-4 border-t border-border">
              <CardTitle className="mb-3 text-sm">Presets de risque</CardTitle>
              <div className="flex gap-2">
                {(presets || ['conservative', 'balanced', 'aggressive']).map((p: string) => (
                  <Button
                    key={p}
                    onClick={() => handlePreset(p)}
                    variant="outline"
                    size="sm"
                  >
                    {p}
                  </Button>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {activeTab === 'notifications' && (
        <Card>
          <CardHeader>
            <CardTitle>Canaux de notification</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {[
                { key: 'telegram_enabled', label: 'Telegram' },
                { key: 'whatsapp_enabled', label: 'WhatsApp' },
                { key: 'email_enabled', label: 'Email' },
              ].map((ch) => {
                const enabled = (config as any).notifications?.[ch.key] || false;
                return (
                  <div key={ch.key} className="flex items-center justify-between p-3 rounded-lg bg-card-hover border border-border">
                    <span className="font-medium">{ch.label}</span>
                    <Badge variant={enabled ? 'success' : 'default'}>
                      {enabled ? 'Activé' : 'Désactivé'}
                    </Badge>
                  </div>
                );
              })}
            </div>
            {/* S4-07 : 3 niveaux de notifications */}
            <div className="mt-4 pt-4 border-t border-border">
              <CardTitle className="mb-2 text-sm">Niveaux de notifications</CardTitle>
              <div className="flex gap-2">
                {['info', 'warning', 'critical'].map((level) => (
                  <Badge
                    key={level}
                    variant={level === 'critical' ? 'danger' : level === 'warning' ? 'warning' : 'info'}
                  >
                    {level}
                  </Badge>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {activeTab === 'exchange' && (
        <Card>
          <CardHeader>
            <CardTitle>Configuration Exchange</CardTitle>
            <Badge variant={config.exchange?.margin ? 'warning' : 'success'}>
              {config.exchange?.margin ? 'Margin' : 'Spot'}
            </Badge>
          </CardHeader>
          <CardContent>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-muted">Exchange</span>
                <span className="font-mono uppercase">{config.exchange?.name || '—'}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">Paper mode</span>
                <Badge variant={config.trading?.paper_mode ? 'success' : 'danger'}>
                  {config.trading?.paper_mode ? 'OUI' : 'NON — LIVE RÉEL'}
                </Badge>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">Max leverage</span>
                <span className="font-mono">{config.trading?.max_leverage || 1}x</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted">API key</span>
                <span className="font-mono text-xs">{config.exchange?.api_key ? '✓ configurée' : '—'}</span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
