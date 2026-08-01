'use client';

// S5-01 / UI-02 fix : configuration multi-symbole.
// L'audit V2 notait que config.html (Jinja2) restait mono-symbole malgré le
// moteur per-symbole. Cette version ajoute un sélecteur de symbole et permet
// de sauvegarder des overrides par symbole pour les paramètres de stratégie
// et l'activation par timeframe.
//
// Lot Réglages : c'était la page `/config`, vers laquelle l'onglet Capital de
// `/settings` se contentait de renvoyer (« Ouvrir la configuration
// avancée »). Ses 4 onglets internes sont devenus 4 sections exportées,
// montées dans les onglets Capital et Notifs. `/config` est en 308 vers
// `/settings?tab=capital`.
//
// Chaque section lit la config elle-même plutôt que de la recevoir en prop :
// react-query dédoublonne la requête, et cela permet de les monter dans des
// onglets différents sans faire remonter l'état dans `/settings`.

import {
  useConfig, useSetStrategyParams, useToggleStrategyTimeframe, useApiStatus,
} from '@/hooks/use-api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import { useState, useMemo, type ReactNode } from 'react';
import { Save } from 'lucide-react';
import { QueryBoundary } from '@/components/ui/query-state';
import { PaperLiveSwitch } from '@/components/cards/paper-live-switch';

type Symbol = string; // ex. "BTC/USDC"

/**
 * S6-12 : le titre reste monté même sans config chargée, et l'échec est
 * affiché/réessayable au lieu d'un spinner qui ne s'arrête jamais.
 */
function ConfigGate({ title, children }: { title: string; children: (config: any) => ReactNode }) {
  const configQuery = useConfig();
  const { data: config } = configQuery;

  if (!config) {
    return (
      <QueryBoundary
        title={<h2 className="text-lg font-semibold tracking-tight">{title}</h2>}
        query={configQuery}
        loadingLabel="Chargement de la configuration…"
        onRetry={() => configQuery.refetch()}
      >
        {null}
      </QueryBoundary>
    );
  }

  return <>{children(config)}</>;
}

// ── Stratégies : éditeur de params par stratégie et par symbole ─────────────

export function ConfigStrategiesView() {
  return (
    <ConfigGate title="Stratégies">
      {(config) => <StrategiesSection config={config} />}
    </ConfigGate>
  );
}

function StrategiesSection({ config }: { config: any }) {
  const { data: status } = useApiStatus();
  const setStrategyParams = useSetStrategyParams();
  const toggleTf = useToggleStrategyTimeframe();

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
    <div className="space-y-4">
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Stratégies</h2>
          <p className="text-sm text-muted mt-1">
            Paramètres par stratégie et activation par timeframe, avec overrides par symbole
          </p>
        </div>
        {/* S5-01 : sélecteur de symbole pour overrides par symbole */}
        <div className="flex items-center gap-2">
          <label className="text-xs text-muted uppercase tracking-wider" htmlFor="config-symbol">
            Symbole
          </label>
          <select
            id="config-symbol"
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
                  {Object.keys(params || {}).filter((k) => k !== 'symbol_overrides').length > 0 && (
                    <div className="space-y-2 mt-3 pt-3 border-t border-border/50">
                      {Object.entries(params)
                        .filter(([k]) => k !== 'symbol_overrides')
                        .slice(0, 3)
                        .map(([k, v]: [string, any]) => (
                          <div key={k} className="flex items-center gap-2">
                            <label
                              className="text-xs text-muted font-mono w-1/2 truncate"
                              htmlFor={`param-${s}-${k}`}
                            >
                              {k}
                            </label>
                            <input
                              id={`param-${s}-${k}`}
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
            <CardTitle className="mb-3 text-sm">
              Timeframes {selectedSymbol !== 'all' && <Badge variant="info" className="ml-2 text-[10px]">{selectedSymbol}</Badge>}
            </CardTitle>
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
  );
}

// ── Risque : valeurs effectives en lecture ─────────────────────────────────
//
// Le bloc « Presets de risque » qui suivait ici a été retiré : il faisait
// `(presets || [...]).map(...)` sur le retour de `usePresets()`, qui est un
// objet `{presets, current, expert_mode}` et non un tableau. `.map` n'existe
// pas dessus : l'onglet Risk de /config plantait au rendu dès que la requête
// aboutissait. Les cartes de preset de l'onglet Capital de /settings
// couvrent le besoin, et correctement.

export function ConfigRiskView() {
  return (
    <ConfigGate title="Paramètres de risque">
      {(config) => (
        <Card>
          <CardHeader>
            <CardTitle>Valeurs de risque effectives</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <div className="text-xs text-muted uppercase tracking-wider">Capital (USDC)</div>
                <div className="text-lg font-mono mt-1">{config.trading?.capital || 1000}</div>
              </div>
              <div>
                <div className="text-xs text-muted uppercase tracking-wider">Risk per trade</div>
                <div className="text-lg font-mono mt-1">{((config.trading?.risk_per_trade || 0.01) * 100).toFixed(1)}%</div>
              </div>
              <div>
                <div className="text-xs text-muted uppercase tracking-wider">Max DD global</div>
                <div className="text-lg font-mono mt-1">{((config.trading?.max_drawdown_global || 0.20) * 100).toFixed(0)}%</div>
              </div>
              <div>
                <div className="text-xs text-muted uppercase tracking-wider">Daily DD limit</div>
                <div className="text-lg font-mono mt-1">{((config.trading?.daily_drawdown_limit || 0.05) * 100).toFixed(0)}%</div>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </ConfigGate>
  );
}

// ── Notifications : état des canaux ────────────────────────────────────────

export function ConfigNotificationsView() {
  return (
    <ConfigGate title="Canaux de notification">
      {(config) => (
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
                const enabled = config.notifications?.[ch.key] || false;
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
    </ConfigGate>
  );
}

// ── Exchange : mode paper/live, levier, clé API ────────────────────────────

export function ConfigExchangeView() {
  return (
    <ConfigGate title="Exchange">
      {(config) => (
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
              {/*
                E3-F1-US8 — ce bloc était en lecture seule : le mode paper/live
                s'affichait mais ne pouvait pas être changé depuis l'UI.
              */}
              <PaperLiveSwitch paperMode={config.trading?.paper_mode} />
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
    </ConfigGate>
  );
}
