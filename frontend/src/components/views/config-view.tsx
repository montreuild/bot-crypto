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

import { useConfig } from '@/hooks/use-api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { ReactNode } from 'react';
import { QueryBoundary } from '@/components/ui/query-state';
import { PaperLiveSwitch } from '@/components/cards/paper-live-switch';

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

// ── Timeframes actifs (Capital) — sans liste des stratégies ─────────────────

export function ConfigTimeframesView() {
  return (
    <ConfigGate title="Timeframes">
      {(config) => (
        <Card>
          <CardHeader>
            <CardTitle>Timeframes actifs</CardTitle>
            <Badge variant="info">{(config.trading?.timeframes || []).length || 0}</Badge>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {(config.trading?.timeframes || []).map((tf: string) => (
                <span
                  key={tf}
                  className="px-3 py-1.5 rounded-lg border border-primary-400 bg-primary-500/10 text-primary-400 text-sm font-medium"
                >
                  {tf}
                  <span className="ml-2 text-xs">✓</span>
                </span>
              ))}
              {(config.trading?.timeframes || []).length === 0 && (
                <span className="text-sm text-muted">Aucun timeframe configuré</span>
              )}
            </div>
            <p className="text-xs text-muted mt-3">
              Timeframes de trading globaux (<code className="font-mono text-xs">trading.timeframes</code>).
              La sélection des bots actifs par TF vient du classement OOS
              (optimizer_results), pas d&apos;une liste manuelle ici.
            </p>
          </CardContent>
        </Card>
      )}
    </ConfigGate>
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

/** Capital de la venue par défaut (S12) — plus de trading.capital global. */
function defaultVenueCapital(config: any): number {
  const envelopes = config?.risk?.envelopes || {};
  const names = Object.keys(envelopes);
  if (names.length === 0) return 0;
  // Préférer la venue crypto live si présente.
  const preferred = envelopes['margin-isolated'] || envelopes[names[0]];
  return Number(preferred?.capital ?? 0);
}

/** Taux de risque par trade depuis risk.profile (S12), jamais trading.risk_per_trade. */
function effectiveTradeRiskPct(config: any): number {
  const risk = config?.risk || {};
  const profile = risk.profile || 'normal';
  const profiles = risk.profiles || { prudent: 0.01, normal: 0.025, agressif: 0.05 };
  const rate = profiles[profile];
  return typeof rate === 'number' ? rate : 0.025;
}

export function ConfigRiskView() {
  return (
    <ConfigGate title="Paramètres de risque">
      {(config) => {
        const capital = defaultVenueCapital(config);
        const tradePct = effectiveTradeRiskPct(config);
        const profile = config.risk?.profile || 'normal';
        return (
          <Card>
            <CardHeader>
              <CardTitle>Valeurs de risque effectives (S12)</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <div className="text-xs text-muted uppercase tracking-wider">Capital venue</div>
                  <div className="text-lg font-mono mt-1">{capital || '—'}</div>
                </div>
                <div>
                  <div className="text-xs text-muted uppercase tracking-wider">Profil / trade</div>
                  <div className="text-lg font-mono mt-1">
                    {profile} · {(tradePct * 100).toFixed(1)}%
                  </div>
                </div>
                <div>
                  <div className="text-xs text-muted uppercase tracking-wider">Max DD global</div>
                  <div className="text-lg font-mono mt-1">
                    {((config.trading?.max_drawdown_global || 0.20) * 100).toFixed(0)}%
                  </div>
                </div>
                <div>
                  <div className="text-xs text-muted uppercase tracking-wider">Daily DD limit</div>
                  <div className="text-lg font-mono mt-1">
                    {((config.trading?.daily_drawdown_limit || 0.05) * 100).toFixed(0)}%
                  </div>
                </div>
              </div>
              <p className="text-[11px] text-muted mt-3">
                Le sizing utilise le % du profil sur l&apos;enveloppe de chaque slot
                (onglet Risque). Les anciens champs risk_per_trade / max_positions
                ne s&apos;appliquent plus.
              </p>
            </CardContent>
          </Card>
        );
      }}
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

// ── Exchange + venues / providers (yfinance, OKX, …) ───────────────────────

export function ConfigExchangeView() {
  return (
    <ConfigGate title="Exchange">
      {(config) => {
        const venues = config.venues || {};
        const defs = venues.defs || {};
        const venueNames: string[] = Object.keys(defs);
        const providers = config.providers || {};
        const yf = providers.yfinance;
        const hasYfinance =
          Boolean(yf) ||
          venueNames.some((n) => String(defs[n]?.data_provider || '').toLowerCase() === 'yfinance');

        return (
          <Card>
            <CardHeader>
              <CardTitle>Configuration Exchange &amp; données</CardTitle>
              <Badge variant={config.exchange?.margin ? 'warning' : 'success'}>
                {config.exchange?.margin ? 'Margin' : 'Spot'}
              </Badge>
            </CardHeader>
            <CardContent>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted">Exchange (crypto)</span>
                  <span className="font-mono uppercase">{config.exchange?.name || '—'}</span>
                </div>
                <PaperLiveSwitch paperMode={config.trading?.paper_mode} />
                <div className="flex justify-between">
                  <span className="text-muted">Venue par défaut</span>
                  <span className="font-mono text-xs">{venues.default || '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">Max leverage</span>
                  <span className="font-mono">{config.trading?.max_leverage || 1}x</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted">API key exchange</span>
                  <span className="font-mono text-xs">
                    {config.exchange?.api_key ? '✓ configurée' : '— (paper / env)'}
                  </span>
                </div>
              </div>

              {/* Providers de données — yfinance pour actions / data-only */}
              <div className="mt-5 pt-4 border-t border-border/60">
                <div className="text-xs text-muted uppercase tracking-wider mb-2">
                  Providers de données
                </div>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between items-center gap-2">
                    <span className="text-muted">yfinance</span>
                    {hasYfinance ? (
                      <Badge variant="success" className="text-[10px]">actif</Badge>
                    ) : (
                      <Badge variant="muted" className="text-[10px]">non déclaré</Badge>
                    )}
                  </div>
                  {yf && (
                    <ul className="text-[11px] text-dim space-y-0.5 font-mono pl-1">
                      <li>suffixe actions : {String(yf.suffix ?? '—')}</li>
                      <li>throttle : {String(yf.min_request_interval ?? '—')} s</li>
                      <li>cache TTL : {String(yf.cache_ttl ?? '—')} s</li>
                    </ul>
                  )}
                  <p className="text-[11px] text-muted">
                    yfinance alimente les venues <code className="font-mono">data_provider: yfinance</code>
                    {' '}(ex. actions Euronext paper) — data-only, sans ordres.
                    Config : <code className="font-mono">providers.yfinance</code> +{' '}
                    <code className="font-mono">venues.defs</code>.
                  </p>
                </div>
              </div>

              <p className="text-[11px] text-muted mt-4 pt-3 border-t border-border/60">
                Les venues et enveloppes de capital sont gérées en tête de l&apos;onglet
                Capital (« Enveloppes par venue »).
              </p>
            </CardContent>
          </Card>
        );
      }}
    </ConfigGate>
  );
}
