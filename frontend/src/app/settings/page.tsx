'use client';

import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn, getStoredTheme, setStoredTheme } from '@/lib/utils';
import {
  enableBrowserNotifications,
  disableBrowserNotifications,
  getBrowserNotificationsPermission,
} from '@/components/notifications-provider';
import { toast } from 'sonner';
import { usePresets, useSetRiskPreset, useSetExpertMode } from '@/hooks/use-api';
import {
  Shield, Sun, Moon, Bell, Github, BookOpen, Loader2, CheckCircle2,
  FlaskConical, Zap, Info,
} from 'lucide-react';

// ── Preset card ─────────────────────────────────────────────────────────────

const PRESET_META: Record<string, { emoji: string; gradient: string }> = {
  prudent: { emoji: '🛡️', gradient: 'from-emerald-500/20 to-emerald-700/10' },
  equilibre: { emoji: '⚖️', gradient: 'from-cyan-500/20 to-cyan-700/10' },
  agressif: { emoji: '🚀', gradient: 'from-red-500/20 to-red-700/10' },
};

function PresetCard({
  presetKey,
  preset,
  isCurrent,
  onSelect,
  disabled,
}: {
  presetKey: string;
  preset: any;
  isCurrent: boolean;
  onSelect: () => void;
  disabled: boolean;
}) {
  const meta = PRESET_META[presetKey] || PRESET_META.equilibre;
  return (
    <button
      onClick={onSelect}
      disabled={disabled}
      className={cn(
        'text-left rounded-xl border bg-card p-5 transition-all relative overflow-hidden',
        'hover:border-primary-400 hover:scale-[1.01]',
        isCurrent && 'border-primary-400 ring-2 ring-primary-400/20',
        !isCurrent && 'border-border',
        disabled && 'opacity-50 cursor-not-allowed hover:scale-100',
      )}
    >
      <div className={cn('absolute inset-0 bg-gradient-to-br opacity-30', meta.gradient)} />
      <div className="relative">
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-3">
            <span className="text-3xl">{meta.emoji}</span>
            <div>
              <h3 className="font-bold text-lg">{preset?.label || presetKey}</h3>
              {isCurrent && (
                <Badge variant="success">
                  <CheckCircle2 className="w-3 h-3" />
                  Actuel
                </Badge>
              )}
            </div>
          </div>
        </div>
        <div className="space-y-1.5 text-sm">
          <Row label="Risk / trade" value={`${((preset?.risk_per_trade || 0) * 100).toFixed(2)}%`} />
          <Row label="Max positions" value={String(preset?.max_positions ?? '—')} />
          <Row label="Daily DD limit" value={`${((preset?.daily_drawdown_limit || 0) * 100).toFixed(1)}%`} />
          <Row label="Global DD" value={`${((preset?.max_drawdown_global || 0) * 100).toFixed(0)}%`} />
          <Row label="Kill switch DD" value={`${((preset?.equity_kill_switch_dd || 0) * 100).toFixed(0)}%`} />
        </div>
      </div>
    </button>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between text-muted">
      <span>{label}</span>
      <span className="font-mono font-semibold text-foreground">{value}</span>
    </div>
  );
}

// ── Toggle switch ───────────────────────────────────────────────────────────

function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      disabled={disabled}
      className={cn(
        'relative w-11 h-6 rounded-full transition-colors flex-shrink-0',
        checked ? 'bg-primary-500' : 'bg-card-hover border border-border',
        disabled && 'opacity-50 cursor-not-allowed',
      )}
    >
      <span
        className={cn(
          'absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform',
          checked ? 'translate-x-5' : 'translate-x-0.5',
        )}
      />
    </button>
  );
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { data: presets, isLoading } = usePresets();
  const setPreset = useSetRiskPreset();
  const setExpertMode = useSetExpertMode();

  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [notifPermission, setNotifPermission] = useState<'default' | 'granted' | 'denied' | 'unsupported'>('default');

  // Init theme + notification permission client-side
  useEffect(() => {
    setTheme(getStoredTheme());
    setNotifPermission(getBrowserNotificationsPermission());
  }, []);

  const handlePreset = async (preset: string) => {
    try {
      await setPreset.mutateAsync(preset);
      toast.success(`Preset "${preset}" appliqué`);
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const handleExpertMode = async (enabled: boolean) => {
    try {
      await setExpertMode.mutateAsync(enabled);
      toast.success(enabled ? 'Expert mode activé' : 'Expert mode désactivé');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const handleThemeToggle = (newTheme: 'dark' | 'light') => {
    setStoredTheme(newTheme);
    setTheme(newTheme);
    toast.success(`Thème ${newTheme === 'dark' ? 'sombre' : 'clair'} activé`);
  };

  const handleEnableNotifications = async () => {
    try {
      const ok = await enableBrowserNotifications();
      if (ok) {
        setNotifPermission('granted');
        toast.success('Notifications navigateur activées — vous recevrez les alertes critiques en temps réel');
      } else if (getBrowserNotificationsPermission() === 'denied') {
        setNotifPermission('denied');
        toast.warning('Notifications refusées par le navigateur — réautoriser depuis les paramètres du site');
      } else {
        toast.info('Demande de permission ignorée');
      }
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const handleDisableNotifications = () => {
    disableBrowserNotifications();
    toast.info('Notifications navigateur désactivées');
  };

  // Enregistre le service worker pour PWA
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch((err) => {
        console.warn('[PWA] Service worker registration failed:', err);
      });
    }
  }, []);

  const presetsObj = (presets?.presets || {}) as Record<string, any>;
  const currentPreset = presets?.current as string | undefined;
  const expertMode = (presets?.expert_mode ?? presets?.expert ?? false) as boolean;

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Réglages</h1>
        <p className="text-sm text-muted mt-1">
          Presets de risque · thème · notifications · à propos
        </p>
      </div>

      {/* Risk presets */}
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted mb-3 flex items-center gap-2">
          <Shield className="w-3.5 h-3.5" /> Presets de risque
        </h2>
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-8 h-8 text-primary-400 animate-spin" />
          </div>
        ) : Object.keys(presetsObj).length === 0 ? (
          <Card>
            <CardContent className="text-center py-8 text-muted text-sm">
              Aucun preset disponible
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {(['prudent', 'equilibre', 'agressif'] as const).map((key) => {
              const preset = presetsObj[key];
              if (!preset) return null;
              return (
                <PresetCard
                  key={key}
                  presetKey={key}
                  preset={preset}
                  isCurrent={currentPreset === key}
                  onSelect={() => handlePreset(key)}
                  disabled={setPreset.isPending}
                />
              );
            })}
            {/* Fallback for any other preset keys */}
            {Object.keys(presetsObj)
              .filter((k) => !['prudent', 'equilibre', 'agressif'].includes(k))
              .map((key) => (
                <PresetCard
                  key={key}
                  presetKey={key}
                  preset={presetsObj[key]}
                  isCurrent={currentPreset === key}
                  onSelect={() => handlePreset(key)}
                  disabled={setPreset.isPending}
                />
              ))}
          </div>
        )}
      </div>

      {/* Expert mode */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FlaskConical className="w-3.5 h-3.5 text-purple-400" />
            Expert mode
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between">
            <div className="pr-4">
              <div className="text-sm font-semibold">Activer le mode expert</div>
              <div className="text-xs text-muted mt-1">
                Débloque les paramètres avancés (slots manuels, optimisation agressive,
                calibration des circuit breakers, etc.). À réserver aux utilisateurs avertis.
              </div>
            </div>
            <Toggle
              checked={expertMode}
              onChange={handleExpertMode}
              disabled={setExpertMode.isPending}
            />
          </div>
        </CardContent>
      </Card>

      {/* Theme */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sun className="w-3.5 h-3.5 text-amber-400" />
            Thème
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3">
            <Button
              variant={theme === 'dark' ? 'primary' : 'outline'}
              onClick={() => handleThemeToggle('dark')}
              className="flex-1"
            >
              <Moon className="w-4 h-4" />
              Sombre
            </Button>
            <Button
              variant={theme === 'light' ? 'primary' : 'outline'}
              onClick={() => handleThemeToggle('light')}
              className="flex-1"
            >
              <Sun className="w-4 h-4" />
              Clair
            </Button>
          </div>
          <div className="text-xs text-muted mt-3">
            Thème actuel: <span className="font-mono font-semibold">{theme}</span>
          </div>
        </CardContent>
      </Card>

      {/* Browser notifications */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bell className="w-3.5 h-3.5 text-primary-400" />
            Notifications navigateur
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between">
            <div className="pr-4">
              <div className="text-sm font-semibold">Autoriser les notifications</div>
              <div className="text-xs text-muted mt-1">
                Recevoir une notification desktop pour les alertes critiques (halt, kill switch,
                drawdown dépassé, etc.).
              </div>
              <div className="mt-2">
                <Badge variant={
                  notifPermission === 'granted' ? 'success' :
                  notifPermission === 'denied' ? 'danger' : 'warning'
                }>
                  {notifPermission === 'granted' ? 'Accordé' :
                   notifPermission === 'denied' ? 'Refusé' : 'En attente'}
                </Badge>
              </div>
            </div>
            {notifPermission === 'granted' ? (
              <Button onClick={handleDisableNotifications} variant="outline">
                <Bell className="w-4 h-4" />
                Désactiver
              </Button>
            ) : (
              <Button
                onClick={handleEnableNotifications}
                disabled={notifPermission === 'denied'}
                variant="primary"
              >
                <Bell className="w-4 h-4" />
                Activer
              </Button>
            )}
          </div>
          {notifPermission === 'denied' && (
            <div className="text-xs text-amber-400 mt-3 flex items-center gap-2">
              <Info className="w-3 h-3" />
              Les notifications ont été refusées. Réinitialisez les permissions du site dans votre navigateur.
            </div>
          )}
          {notifPermission === 'granted' && (
            <div className="text-xs text-emerald-400 mt-3 flex items-center gap-2">
              <CheckCircle2 className="w-3 h-3" />
              Notifications natives activées — alertes temps réel (trade fermé, circuit breaker) reçues même sur d&apos;autres onglets.
            </div>
          )}
        </CardContent>
      </Card>

      {/* About */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Zap className="w-3.5 h-3.5 text-primary-400" />
            À propos
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
            <div>
              <div className="text-xs text-dim mb-1">Version</div>
              <div className="font-mono font-semibold">v12.17.0</div>
            </div>
            <div>
              <div className="text-xs text-dim mb-1">Stack</div>
              <div className="font-mono font-semibold">Next.js 15 · React 19 · TS 5.7</div>
            </div>
            <div>
              <div className="text-xs text-dim mb-1">Mode</div>
              <div className="font-mono font-semibold">Paper / Live</div>
            </div>
          </div>
          <div className="flex items-center gap-3 mt-5">
            <a
              href="https://github.com"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border hover:border-border-hi text-sm transition-colors"
            >
              <Github className="w-4 h-4" />
              GitHub
            </a>
            <a
              href="https://docs.example.com"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border hover:border-border-hi text-sm transition-colors"
            >
              <BookOpen className="w-4 h-4" />
              Documentation
            </a>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
