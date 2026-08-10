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

import { useState, useEffect, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  RiskFeasibilityPanel,
  VenueEnvelopesEditor,
} from '@/components/views/risk-envelopes-editor';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { cn, getStoredTheme, setStoredTheme } from '@/lib/utils';
import { usePresets, useSetRiskPreset, useSetExpertMode, useBotStatus } from '@/hooks/use-api';
import { toast } from 'sonner';
import {
  Wallet, Bell, Database, ScrollText, ShieldAlert, Settings as SettingsIcon,
  Check, Loader2, Send, Sun, Moon, SlidersHorizontal,
} from 'lucide-react';
import { UniverseManager } from '@/components/cards/universe-manager';
import { StrategyParamsPanel } from '@/components/cards/strategy-params-panel';
import {
  enableBrowserNotifications,
  disableBrowserNotifications,
  getBrowserNotificationsPermission,
  isBrowserNotificationsEnabled,
} from '@/components/notifications-provider';
import {
  ConfigTimeframesView,
  ConfigNotificationsView,
  ConfigExchangeView,
} from '@/components/views/config-view';
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

/**
 * Repli UI tant que `/api/settings/presets` n'a pas répondu.
 * Le sizing = risk.profile × enveloppe de slot — plus de risk_per_trade
 * global ni max_positions. Les taux ci-dessous suivent risk.profiles livrés.
 */
const PRESETS = [
  {
    key: 'prudent',
    label: 'Prudent',
    description: 'Risque minimal — 1 % de l’enveloppe de chaque slot',
    profile: 'prudent' as string | null,
    trade_risk_pct: 0.01 as number | null,
    daily_dd: 0.03 as number | null,
    global_dd: 0.15 as number | null,
    kill_switch: 0.25 as number | null,
    custom: false,
  },
  {
    key: 'equilibre',
    label: 'Équilibré',
    description: 'Profil normal — 2,5 % de l’enveloppe de chaque slot',
    profile: 'normal' as string | null,
    trade_risk_pct: 0.025 as number | null,
    daily_dd: 0.05 as number | null,
    global_dd: 0.20 as number | null,
    kill_switch: 0.35 as number | null,
    custom: false,
  },
  {
    key: 'agressif',
    label: 'Agressif',
    description: 'Risque élevé — 5 % de l’enveloppe de chaque slot',
    profile: 'agressif' as string | null,
    trade_risk_pct: 0.05 as number | null,
    daily_dd: 0.08 as number | null,
    global_dd: 0.30 as number | null,
    kill_switch: 0.45 as number | null,
    custom: false,
  },
  {
    key: 'personnalise',
    label: 'Personnalisé',
    description:
      'Déverrouille les budgets de perte (risque symbole / venue). Capital et exposition max restent toujours éditables.',
    profile: null as string | null,
    trade_risk_pct: null as number | null,
    daily_dd: null as number | null,
    global_dd: null as number | null,
    kill_switch: null as number | null,
    custom: true,
  },
] as const;

function mergePreset(fallback: (typeof PRESETS)[number], remote: any) {
  if (!remote) return { ...fallback };
  const pick = (value: unknown, dflt: number | null) =>
    value == null ? dflt : Number(value);
  return {
    ...fallback,
    label: remote.label || fallback.label,
    description: remote.description || fallback.description,
    profile: remote.profile ?? fallback.profile,
    custom: Boolean(remote.custom ?? fallback.custom),
    trade_risk_pct: pick(remote.trade_risk_pct, fallback.trade_risk_pct),
    daily_dd: pick(remote.daily_drawdown_limit, fallback.daily_dd),
    global_dd: pick(remote.max_drawdown_global, fallback.global_dd),
    kill_switch: pick(remote.equity_kill_switch_dd, fallback.kill_switch),
  };
}

const TABS = ['capital', 'risk', 'strategies', 'notifications', 'data', 'audit', 'ui'] as const;

export default function SettingsV2Page() {
  return (
    <Suspense fallback={<div className="p-6 text-muted">Chargement…</div>}>
      <SettingsV2Content />
    </Suspense>
  );
}

function SettingsV2Content() {
  const searchParams = useSearchParams();
  // Un `?tab=` inconnu retombe sur Capital plutôt que d'afficher un onglet vide.
  const requestedTab = searchParams.get('tab');
  const [tab, setTab] = useState<string>(
    TABS.includes(requestedTab as (typeof TABS)[number]) ? requestedTab! : 'capital',
  );
  const presetsQuery = usePresets();
  const setPreset = useSetRiskPreset();
  const setExpertMode = useSetExpertMode();
  const { data: status } = useBotStatus();
  const { data: presets } = presetsQuery;
  const currentPreset = presets?.current || 'equilibre';
  const expertMode = presets?.expert_mode || false;
  const gitCommit = (status as any)?.git_commit as string | null | undefined;

  // Lot Réglages — thème et permission de notification, portés depuis
  // /settings. Lus côté client uniquement (localStorage / API Notification).
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [notifPermission, setNotifPermission] =
    useState<'default' | 'granted' | 'denied' | 'unsupported'>('default');
  const [notifEnabled, setNotifEnabled] = useState(false);

  useEffect(() => {
    setTheme(getStoredTheme());
    setNotifPermission(getBrowserNotificationsPermission());
    setNotifEnabled(isBrowserNotificationsEnabled());
  }, []);

  const handleThemeToggle = (newTheme: 'dark' | 'light') => {
    setStoredTheme(newTheme);
    setTheme(newTheme);
    toast.success(`Thème ${newTheme === 'dark' ? 'sombre' : 'clair'} activé`);
  };

  const handleBrowserNotifToggle = async (enabled: boolean) => {
    if (!enabled) {
      disableBrowserNotifications();
      setNotifEnabled(false);
      toast.info('Notifications navigateur désactivées');
      return;
    }
    try {
      const ok = await enableBrowserNotifications();
      // La permission a pu changer pendant la demande : on la relit plutôt que
      // de la déduire du booléen.
      setNotifPermission(getBrowserNotificationsPermission());
      setNotifEnabled(ok);
      if (ok) {
        toast.success('Notifications navigateur activées — alertes critiques en temps réel');
      } else if (getBrowserNotificationsPermission() === 'denied') {
        toast.warning('Notifications refusées par le navigateur — réautoriser depuis les paramètres du site');
      } else {
        toast.info('Demande de permission ignorée');
      }
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    }
  };

  const header = (
    <div>
      <h1 className="text-2xl font-bold tracking-tight">Réglages</h1>
      <p className="text-sm text-muted mt-1">
        Capital (timeframes, exchange/yfinance), risque (profil, enveloppes),
        notifications, données, audit et préférences UI
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
        <TabsList className="grid grid-cols-7 w-full max-w-3xl">
          <TabsTrigger value="capital">
            <Wallet className="w-3.5 h-3.5 mr-1.5" />
            Capital
          </TabsTrigger>
          <TabsTrigger value="risk">
            <ShieldAlert className="w-3.5 h-3.5 mr-1.5" />
            Risque
          </TabsTrigger>
          <TabsTrigger value="strategies">
            <SlidersHorizontal className="w-3.5 h-3.5 mr-1.5" />
            Stratégies
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
          {/* Enveloppes en tête (venues déclarées fusionnées) ; timeframes ; exchange. */}
          <VenueEnvelopesEditor />
          <ConfigTimeframesView />
          <ConfigExchangeView />
        </TabsContent>

        <TabsContent value="risk" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Profil de risque</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
                {PRESETS.map((fallback) => {
                  const p = mergePreset(fallback, presets?.presets?.[fallback.key]);
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
                      {!p.custom && (
                        <div className="space-y-1 text-[11px]">
                          <div className="flex justify-between">
                            <span className="text-dim">Profil moteur</span>
                            <span className="font-mono">{p.profile}</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-dim">Risque / trade</span>
                            <span className="font-mono">
                              {p.trade_risk_pct != null
                                ? `${(p.trade_risk_pct * 100).toFixed(1)}% slot`
                                : '—'}
                            </span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-dim">Daily DD</span>
                            <span className="font-mono">
                              {p.daily_dd != null ? `${(p.daily_dd * 100).toFixed(0)}%` : '—'}
                            </span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-dim">Global DD</span>
                            <span className="font-mono">
                              {p.global_dd != null ? `${(p.global_dd * 100).toFixed(0)}%` : '—'}
                            </span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-dim">Kill-switch</span>
                            <span className="font-mono">
                              {p.kill_switch != null
                                ? `${(p.kill_switch * 100).toFixed(0)}%`
                                : '—'}
                            </span>
                          </div>
                        </div>
                      )}
                      {p.custom && (
                        <p className="text-[11px] text-dim">
                          Active l&apos;édition libre des budgets de perte sur Capital → Enveloppes
                          (exposition max / symbole reste toujours éditable).
                        </p>
                      )}
                    </button>
                  );
                })}
              </div>
            </CardContent>
          </Card>

          <RiskFeasibilityPanel />
        </TabsContent>

        {/* P0-3 : éditeur de strategy_params — la route et le hook existaient,
            il manquait l'écran qui les appelle. */}
        <TabsContent value="strategies" className="space-y-4">
          <StrategyParamsPanel />
        </TabsContent>

        <TabsContent value="notifications" className="space-y-4">
          <ConfigNotificationsView />
          <Card>
            <CardContent className="p-4 flex items-center justify-between gap-4 flex-wrap">
              <p className="text-sm text-muted">
                Telegram, WhatsApp (CallMeBot/Twilio), Email SMTP. Les identifiants se
                règlent dans <code className="font-mono text-xs">config.yaml</code>.
              </p>
              {/* S9-F3-US4 */}
              <TestNotificationButton />
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

              {/*
                Lot Réglages — ce switch était un `defaultChecked` sans
                handler : il affichait « activé » sans jamais demander la
                permission au navigateur ni écrire quoi que ce soit. C'était
                le seul blocage réel de la 308 de /settings, qui portait la
                vraie implémentation. Il est désormais câblé sur les helpers
                de `notifications-provider`, et reflète l'état de permission
                réel plutôt qu'un défaut optimiste.
              */}
              <div className="flex items-center justify-between pt-3 border-t border-border">
                <div className="pr-4">
                  <Label htmlFor="browser-notif">Notifications navigateur</Label>
                  <p className="text-xs text-muted mt-0.5">
                    Alertes desktop sur risk.critical et trade.closed
                  </p>
                  <div className="mt-2">
                    <Badge variant={
                      notifPermission === 'granted' ? 'success'
                        : notifPermission === 'denied' ? 'danger'
                          : notifPermission === 'unsupported' ? 'muted' : 'warning'
                    }>
                      {notifPermission === 'granted' ? 'Accordé'
                        : notifPermission === 'denied' ? 'Refusé'
                          : notifPermission === 'unsupported' ? 'Non supporté' : 'En attente'}
                    </Badge>
                  </div>
                  {notifPermission === 'denied' && (
                    <p className="text-xs text-amber-400 mt-2">
                      Refusées par le navigateur : réinitialisez les permissions du site
                      pour pouvoir les réactiver ici.
                    </p>
                  )}
                </div>
                <Switch
                  id="browser-notif"
                  checked={notifEnabled}
                  onCheckedChange={handleBrowserNotifToggle}
                  // `denied` et `unsupported` ne sont pas récupérables depuis la
                  // page : demander la permission ne rouvrirait aucun dialogue.
                  disabled={notifPermission === 'denied' || notifPermission === 'unsupported'}
                />
              </div>

              {/*
                Lot Réglages — le thème renvoyait vers la topbar par un badge
                inerte, alors que /settings offrait un vrai choix explicite.
                Le contrôle est porté ici ; la topbar garde son raccourci.
              */}
              <div className="flex items-center justify-between pt-3 border-t border-border gap-4">
                <div>
                  <Label>Thème</Label>
                  <p className="text-xs text-muted mt-0.5">
                    Sombre / Clair — également accessible depuis la topbar
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant={theme === 'dark' ? 'primary' : 'outline'}
                    size="sm"
                    onClick={() => handleThemeToggle('dark')}
                    aria-pressed={theme === 'dark'}
                  >
                    <Moon className="w-3.5 h-3.5" />
                    Sombre
                  </Button>
                  <Button
                    variant={theme === 'light' ? 'primary' : 'outline'}
                    size="sm"
                    onClick={() => handleThemeToggle('light')}
                    aria-pressed={theme === 'light'}
                  >
                    <Sun className="w-3.5 h-3.5" />
                    Clair
                  </Button>
                </div>
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
              <div className="flex justify-between gap-4">
                <span>Commit Git</span>
                <span className="font-mono truncate" title={gitCommit || undefined}>
                  {gitCommit || '—'}
                </span>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
