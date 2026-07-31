'use client';

/**
 * S6-F5-US1 — Page Réglages v2 (fusion /settings + /config + /data + /audit-log admin).
 *
 * Stratégie strangler fig : coexiste avec les pages existantes.
 *
 * Sections :
 *  - Capital & Risque (presets, capital, paper/live, params avancés si expert)
 *  - Notifications (Telegram/WhatsApp/Email + test button)
 *  - Données & Univers (univers management + backfill)
 *  - Audit & Conformité (journal d'audit filtrable)
 *  - Préférences UI (thème, locale, mode expert, notifications navigateur)
 */

import { useState } from 'react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { usePresets, useSetRiskPreset, useSetExpertMode, useConfig } from '@/hooks/use-api';
import { toast } from 'sonner';
import {
  Wallet, Bell, Database, ScrollText, Settings as SettingsIcon,
  Shield, Check, AlertTriangle, Loader2, Send,
} from 'lucide-react';
import { UniverseManager } from '@/components/cards/universe-manager';
import { api } from '@/lib/api';

/**
 * S9-F3-US4 — Bouton « Tester l'envoi de notification ».
 * Consomme POST /api/config/notifications/test.
 */
function TestNotificationButton() {
  const [loading, setLoading] = useState(false);
  const handleTest = async () => {
    setLoading(true);
    try {
      await api.testNotifications();
      toast.success('Notification de test envoyée — vérifiez vos canaux');
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    } finally {
      setLoading(false);
    }
  };
  return (
    <Button variant="outline" onClick={handleTest} disabled={loading}>
      {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
      Tester l&apos;envoi
    </Button>
  );
}

const PRESETS = [
  {
    key: 'prudent',
    label: 'Prudent',
    description: 'Risque minimal, capital préservé',
    risk_per_trade: 0.005,
    max_positions: 3,
    daily_dd: 0.03,
    global_dd: 0.10,
    kill_switch: 0.25,
  },
  {
    key: 'equilibre',
    label: 'Équilibré',
    description: 'Risque modéré, croissance équilibrée',
    risk_per_trade: 0.01,
    max_positions: 5,
    daily_dd: 0.05,
    global_dd: 0.20,
    kill_switch: 0.35,
  },
  {
    key: 'agressif',
    label: 'Agressif',
    description: 'Risque élevé, croissance maximale',
    risk_per_trade: 0.02,
    max_positions: 8,
    daily_dd: 0.08,
    global_dd: 0.30,
    kill_switch: 0.50,
  },
] as const;

export default function SettingsV2Page() {
  const [tab, setTab] = useState('capital');
  const presetsQuery = usePresets();
  const setPreset = useSetRiskPreset();
  const setExpertMode = useSetExpertMode();
  const { data: presets } = presetsQuery;
  const currentPreset = presets?.current || 'equilibre';
  const expertMode = presets?.expert_mode || false;

  const header = (
    <div>
      <h1 className="text-2xl font-bold tracking-tight">Réglages</h1>
      <p className="text-sm text-muted mt-1">
        Capital, risque, notifications, données, audit et préférences UI
      </p>
    </div>
  );

  const handlePreset = async (preset: string) => {
    try {
      await setPreset.mutateAsync(preset);
      toast.success(`Preset ${preset} appliqué`);
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    }
  };

  const handleExpertToggle = async (enabled: boolean) => {
    try {
      await setExpertMode.mutateAsync(enabled);
      if (typeof window !== 'undefined') {
        localStorage.setItem('expert_mode', String(enabled));
      }
      toast.success(`Mode expert ${enabled ? 'activé' : 'désactivé'}`);
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    }
  };

  return (
    <div className="space-y-6 animate-fade-in">
      {header}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid grid-cols-5 w-full max-w-2xl">
          <TabsTrigger value="capital">
            <Wallet className="w-3.5 h-3.5 mr-1.5" />
            Capital
          </TabsTrigger>
          <TabsTrigger value="notifications">
            <Bell className="w-3.5 h-3.5 mr-1.5" />
            Notifs
          </TabsTrigger>
          <TabsTrigger value="data">
            <Database className="w-3.5 h-3.5 mr-1.5" />
            Données
          </TabsTrigger>
          <TabsTrigger value="audit">
            <ScrollText className="w-3.5 h-3.5 mr-1.5" />
            Audit
          </TabsTrigger>
          <TabsTrigger value="ui">
            <SettingsIcon className="w-3.5 h-3.5 mr-1.5" />
            UI
          </TabsTrigger>
        </TabsList>

        <TabsContent value="capital" className="space-y-4">
          {/* Presets de risque */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Profil de risque</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {PRESETS.map((p) => {
                  const isActive = currentPreset === p.key;
                  return (
                    <button
                      key={p.key}
                      onClick={() => handlePreset(p.key)}
                      disabled={setPreset.isPending}
                      className={cn(
                        'text-left p-4 rounded-lg border transition-all',
                        isActive
                          ? 'border-primary-400 ring-2 ring-primary-400/20 bg-primary-500/5'
                          : 'border-border bg-card hover:border-border-hi',
                      )}
                      aria-pressed={isActive}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-semibold text-sm">{p.label}</span>
                        {isActive && (
                          <Badge variant="success" className="text-[10px]">
                            <Check className="w-3 h-3" />
                            Actif
                          </Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted mb-3">{p.description}</p>
                      <div className="space-y-1 text-[11px]">
                        <div className="flex justify-between">
                          <span className="text-dim">Risk/trade</span>
                          <span className="font-mono">{(p.risk_per_trade * 100).toFixed(1)}%</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-dim">Max positions</span>
                          <span className="font-mono">{p.max_positions}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-dim">Daily DD</span>
                          <span className="font-mono">{(p.daily_dd * 100).toFixed(0)}%</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-dim">Global DD</span>
                          <span className="font-mono">{(p.global_dd * 100).toFixed(0)}%</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-dim">Kill-switch</span>
                          <span className="font-mono">{(p.kill_switch * 100).toFixed(0)}%</span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </CardContent>
          </Card>

          {/* Paramètres avancés (redirection vers /config) */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2">
                <Shield className="w-4 h-4 text-amber-400" />
                Paramètres avancés
                {!expertMode && <Badge variant="muted" className="text-[10px]">Expert requis</Badge>}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted mb-3">
                Score threshold, risk per trade, max positions, paper slippage, circuit breakers
                par slot, margin, params par stratégie.
              </p>
              <Button
                variant="outline"
                onClick={() => window.location.href = '/config'}
                disabled={!expertMode}
              >
                Ouvrir la configuration avancée
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="notifications" className="space-y-4">
          <Card>
            <CardContent className="p-8 text-center">
              <Bell className="w-10 h-10 mx-auto text-primary-400 mb-3" />
              <h3 className="text-base font-semibold mb-1">Notifications</h3>
              <p className="text-sm text-muted max-w-md mx-auto mb-4">
                Telegram, WhatsApp (CallMeBot/Twilio), Email SMTP. 3 niveaux
                (info/warning/critical).
              </p>
              <div className="flex justify-center gap-2">
                <Button variant="outline" onClick={() => window.location.href = '/config'}>
                  Configurer
                </Button>
                <TestNotificationButton />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="data" className="space-y-4">
          {/* S8-F2-US1/2/3 — Gestionnaire d'univers inline */}
          <UniverseManager />

          <Card>
            <CardContent className="p-6 text-center">
              <Database className="w-8 h-8 mx-auto text-primary-400 mb-2" />
              <h3 className="text-base font-semibold mb-1">Données OHLCV</h3>
              <p className="text-sm text-muted max-w-md mx-auto mb-3">
                Cache OHLCV, refetch manuel, backfill yfinance async.
              </p>
              <Button variant="outline" onClick={() => window.location.href = '/data'}>
                Gérer les bougies OHLCV
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="audit">
          <Card>
            <CardContent className="p-8 text-center">
              <ScrollText className="w-10 h-10 mx-auto text-primary-400 mb-3" />
              <h3 className="text-base font-semibold mb-1">Audit & Conformité</h3>
              <p className="text-sm text-muted max-w-md mx-auto mb-4">
                Journal d&apos;audit filtrable (action/actor). Stats par action. Export CSV.
                Conformité future MiCA/AMF/SEC (Sprint 12).
              </p>
              <Button variant="outline" onClick={() => window.location.href = '/audit-log'}>
                Consulter le journal
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="ui" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Préférences UI</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Mode expert */}
              <div className="flex items-center justify-between">
                <div>
                  <Label htmlFor="expert-mode">Mode expert</Label>
                  <p className="text-xs text-muted mt-0.5">
                    Révèle les seuils avancés (CBs par slot, ML params, walk-forward folds)
                  </p>
                </div>
                <Switch
                  id="expert-mode"
                  checked={expertMode}
                  onCheckedChange={handleExpertToggle}
                  disabled={setExpertMode.isPending}
                />
              </div>

              {/* Notifications navigateur (placeholder) */}
              <div className="flex items-center justify-between pt-3 border-t border-border">
                <div>
                  <Label htmlFor="browser-notif">Notifications navigateur</Label>
                  <p className="text-xs text-muted mt-0.5">
                    Alertes desktop sur risk.critical et trade.closed
                  </p>
                </div>
                <Switch id="browser-notif" defaultChecked />
              </div>

              {/* Thème (placeholder — géré par topbar) */}
              <div className="flex items-center justify-between pt-3 border-t border-border">
                <div>
                  <Label>Thème</Label>
                  <p className="text-xs text-muted mt-0.5">
                    Sombre / Clair / Système — toggle dans la topbar
                  </p>
                </div>
                <Badge variant="muted">Topbar</Badge>
              </div>
            </CardContent>
          </Card>

          {/* À propos */}
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">À propos</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs text-muted">
              <div className="flex justify-between">
                <span>Version</span>
                <span className="font-mono">v12.17</span>
              </div>
              <div className="flex justify-between">
                <span>Stack</span>
                <span className="font-mono">Next.js 15 · React 19 · TS 5.7</span>
              </div>
              <div className="flex justify-between">
                <span>Repository</span>
                <span className="font-mono">montreuild/bot-crypto</span>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
