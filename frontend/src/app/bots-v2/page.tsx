'use client';

/**
 * S4-F2-US1 — Page Mes Bots v2 (fusion /bots + /config-stratégies + drawer enrichi).
 *
 * Stratégie strangler fig : coexiste avec /bots existant.
 *
 * Améliorations vs /bots actuel :
 *  - Card bot enrichie : sparkline 7j (TODO), indicateur de confiance (🟢🟠🔴),
 *    budget continu slider (TODO)
 *  - Drawer latéral au clic sur une card (Radix Dialog side variant)
 *    contenant : frise cycle de vie + cône Monte-Carlo + actions contextuelles
 *  - Bouton « Recruter un nouveau bot » → redirige vers /lab?intent=create
 *  - Filtre « forcés en actif » (bots manual_active: true)
 *  - Kanban 4 colonnes (Candidats/Essai/Actifs/Retirés) + filtre
 */

import { useBots, useOosTracker, useForceBotActive, useRunForwardTest, useResetSlot } from '@/hooks/use-api';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from '@/components/ui/dialog';
import { cn, lifecycleStyle, parseSlotKey } from '@/lib/utils';
import { toast } from 'sonner';
import {
  Bot as BotIcon, RefreshCw, Star, AlertCircle, Plus, X,
  TrendingUp, TrendingDown, Minus, Activity,
} from 'lucide-react';
import { useState, useMemo, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { QueryBoundary, EmptyState } from '@/components/ui/query-state';
import { LifecycleFrieze } from '@/components/cards/lifecycle-frieze';
import { MonteCarloCone } from '@/components/cards/monte-carlo-cone';
import type { Bot } from '@/types';

const COLUMN_STATES = ['candidat', 'essai', 'actif', 'retire'] as const;

// Indicateur de confiance basé sur edge + forward-test + realization live
function getConfidence(bot: Bot): { level: 'high' | 'medium' | 'low'; icon: string; color: string } {
  const edge = bot.edge;
  if (!edge || !edge.available) {
    return { level: 'low', icon: '🔴', color: 'text-red-400' };
  }
  const ciLow = edge.ci_low_pct ?? 0;
  const n = edge.n ?? 0;
  if (ciLow > 0.5 && n >= 15) return { level: 'high', icon: '🟢', color: 'text-emerald-400' };
  if (ciLow > 0 && n >= 5) return { level: 'medium', icon: '🟠', color: 'text-amber-400' };
  return { level: 'low', icon: '🔴', color: 'text-red-400' };
}

export default function BotsV2Page() {
  return (
    <Suspense fallback={<div className="p-6 text-muted">Chargement…</div>}>
      <BotsV2Content />
    </Suspense>
  );
}

function BotsV2Content() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = useBots();
  const oosQuery = useOosTracker();
  const forceActive = useForceBotActive();
  const runForward = useRunForwardTest();
  const resetSlot = useResetSlot();
  const { data } = query;
  const { data: oosData } = oosQuery;

  const [filter, setFilter] = useState<string>('all');
  // `manual_active` (cf. app/api/routes/portfolio.py:152) vaut `true` quand le
  // slot est FORCÉ en actif via `lifecycle.manual_active` — `false` est donc
  // l'état normal de tout bot piloté par le cycle de vie automatique, pas un
  // « gel ». Le filtre d'origine (`showFrozen`) masquait par défaut tout bot
  // `manual_active !== true` : sur un déploiement réel (240 candidats, aucun
  // forçage) le kanban s'affichait entièrement vide alors que l'en-tête
  // annonçait « 240 candidats ». On expose maintenant la sémantique réelle.
  const [onlyForced, setOnlyForced] = useState(false);

  // Slot sélectionné via URL (?slot=...) ou clic sur card
  const selectedSlotKey = searchParams.get('slot');
  const [drawerOpen, setDrawerOpen] = useState(!!selectedSlotKey);

  const header = (
    <div className="flex items-end justify-between flex-wrap gap-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Mes Bots</h1>
        <p className="text-sm text-muted mt-1">
          Portefeuille de stratégies · cycle de vie automatique · override manuel possible
        </p>
      </div>
      <Button onClick={() => router.push('/lab?intent=create')}>
        <Plus className="w-4 h-4" />
        Recruter un nouveau bot
      </Button>
    </div>
  );

  // Ces useMemo étaient placés APRÈS le retour anticipé `if (!data)` ci-dessous.
  // Au premier render (requête en vol) React n'enregistrait donc pas ces deux
  // hooks, puis les voyait apparaître une fois les données arrivées : « Rendered
  // more hooks than during the previous render » — la page plantait dès que
  // /api/bots répondait. Tous les hooks sont maintenant appelés inconditionnellement.
  // `bots` est mémoïsé pour rester une référence stable en dépendance.
  const bots = useMemo<Bot[]>(() => data?.bots || [], [data]);

  // Filtres : état + override manuel
  const filtered = useMemo(() => {
    let result = filter === 'all' ? bots : bots.filter((b) => b.state === filter);
    if (onlyForced) {
      result = result.filter((b) => b.manual_active === true);
    }
    return result;
  }, [bots, filter, onlyForced]);

  // Grouper par colonne (kanban)
  const botsByColumn = useMemo(() => {
    const map: Record<string, Bot[]> = { candidat: [], essai: [], actif: [], retire: [] };
    filtered.forEach((b) => {
      if (map[b.state]) map[b.state].push(b);
    });
    return map;
  }, [filtered]);

  if (!data) {
    return (
      <QueryBoundary
        title={header}
        query={query}
        loadingLabel="Chargement des bots…"
        onRetry={() => query.refetch()}
      >
        {null}
      </QueryBoundary>
    );
  }

  const counts = data.counts || {};

  // Bot sélectionné (pour drawer)
  const selectedBot = bots.find((b) => b.slot_key === selectedSlotKey);
  // `/api/oos-tracker` renvoie `slots` sous forme de **dictionnaire** indexé par
  // slot_key, pas de tableau. Le `.find()` d'origine levait donc
  // « oosData.slots.find is not a function » : ouvrir un bot faisait tomber
  // toute la page dans l'ErrorBoundary — le drawer (frise + cône Monte-Carlo),
  // c'est-à-dire le cœur de S4, n'a jamais pu s'afficher. On gère les deux
  // formes pour rester robuste si le contrat évolue.
  const selectedOosSlot = (() => {
    const slots = oosData?.slots;
    if (!selectedSlotKey || !slots) return undefined;
    return Array.isArray(slots)
      ? slots.find((s: any) => s.slot_key === selectedSlotKey)
      : slots[selectedSlotKey];
  })();

  const handleCardClick = (bot: Bot) => {
    setDrawerOpen(true);
    const params = new URLSearchParams(searchParams);
    params.set('slot', bot.slot_key);
    router.replace(`/bots-v2?${params.toString()}`, { scroll: false });
  };

  const handleCloseDrawer = () => {
    setDrawerOpen(false);
    router.replace('/bots-v2', { scroll: false });
  };

  const handleForce = async (slotKey: string, enabled: boolean) => {
    try {
      await forceActive.mutateAsync({ slotKey, enabled });
      toast.success(enabled ? 'Bot forcé en ACTIF' : 'Forçage levé');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const handleForward = async (slotKey: string) => {
    try {
      toast.info('Forward-test en cours...');
      await runForward.mutateAsync(slotKey);
      toast.success('Forward-test terminé');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const handleReset = async (slotKey: string) => {
    try {
      await resetSlot.mutateAsync(slotKey);
      toast.success('Circuit breaker du slot réinitialisé');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  return (
    <div className="space-y-6 animate-fade-in">
      {header}

      {/* Lifecycle counts (filtres) */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {COLUMN_STATES.map((state) => {
          const style = lifecycleStyle(state);
          const count = counts[state] || 0;
          return (
            <button
              key={state}
              onClick={() => setFilter(filter === state ? 'all' : state)}
              className={cn(
                'p-4 rounded-xl border bg-card text-left transition-all hover:scale-[1.02]',
                filter === state ? 'border-primary-400 ring-2 ring-primary-400/20' : 'border-border',
                style.bg,
              )}
              aria-pressed={filter === state}
            >
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-xs uppercase tracking-wider text-dim">{style.label}</div>
                  <div className={cn('text-2xl font-bold mt-1', style.text)}>{count}</div>
                </div>
                <span className="text-2xl opacity-50" aria-hidden>{style.icon}</span>
              </div>
            </button>
          );
        })}
      </div>

      {/* Toggle « forçage manuel » */}
      <div className="flex items-center gap-3 text-xs">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={onlyForced}
            onChange={(e) => setOnlyForced(e.target.checked)}
            className="rounded border-border"
          />
          <span className="text-muted">
            N&apos;afficher que les bots forcés en actif (override manuel)
          </span>
        </label>
      </div>

      {/* Kanban 4 colonnes */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {COLUMN_STATES.map((state) => {
          const style = lifecycleStyle(state);
          const columnBots = botsByColumn[state] || [];
          return (
            <div key={state} className="space-y-3">
              <div className={cn('flex items-center gap-2 px-3 py-2 rounded-md text-xs font-semibold uppercase tracking-wider', style.bg, style.text)}>
                <span aria-hidden>{style.icon}</span>
                {style.label}
                <span className="ml-auto text-dim">{columnBots.length}</span>
              </div>
              <div className="space-y-3 min-h-[100px]">
                {columnBots.length === 0 ? (
                  <div className="text-center text-xs text-dim py-8">—</div>
                ) : (
                  columnBots.map((bot) => (
                    <BotCardV2
                      key={bot.slot_key}
                      bot={bot}
                      onClick={() => handleCardClick(bot)}
                      onForce={() => handleForce(bot.slot_key, !bot.manual_active)}
                      onForward={() => handleForward(bot.slot_key)}
                      forceLoading={forceActive.isPending}
                      forwardLoading={runForward.isPending}
                    />
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>

      {filtered.length === 0 && (
        <EmptyState
          icon={AlertCircle}
          label="Aucun bot dans cet état"
          description="Essayez un autre filtre ou décochez « bots forcés en actif »"
        />
      )}

      {/* Drawer latéral (Radix Dialog side) */}
      <Dialog open={drawerOpen} onOpenChange={(open) => !open && handleCloseDrawer()}>
        <DialogContent className="sm:max-w-[460px] min-h-[80vh] max-h-[90vh] overflow-y-auto">
          {selectedBot && (
            <>
              <DialogHeader>
                <div className="flex items-center gap-2 mb-1">
                  <BotIcon className="w-4 h-4 text-primary-400" />
                  <DialogTitle className="font-mono text-base">
                    {parseSlotKey(selectedBot.slot_key).strategy}
                    <span className="text-muted font-normal">
                      {' '}::{parseSlotKey(selectedBot.slot_key).tf}
                      {selectedBot.symbol ? `::${selectedBot.symbol}` : ''}
                    </span>
                  </DialogTitle>
                </div>
                <DialogDescription className="flex items-center gap-2">
                  <Badge variant={
                    selectedBot.state === 'actif' ? 'success' :
                    selectedBot.state === 'essai' ? 'info' :
                    selectedBot.state === 'retire' ? 'danger' : 'warning'
                  }>
                    {lifecycleStyle(selectedBot.state).label}
                  </Badge>
                  {selectedBot.manual_active === true && (
                    <Badge variant="warning">Forcé en actif (manuel)</Badge>
                  )}
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-5 mt-4">
                {/* Frise de cycle de vie */}
                <LifecycleFrieze
                  currentState={selectedBot.state}
                  transitions={[]}
                />

                {/* Cône Monte-Carlo */}
                <MonteCarloCone
                  slotKey={selectedBot.slot_key}
                  oosData={selectedOosSlot}
                />

                {/* Stats edge */}
                {selectedBot.edge && selectedBot.edge.available && (
                  <div className="grid grid-cols-3 gap-3 p-3 rounded-lg border border-border bg-card">
                    <div>
                      <div className="text-[10px] text-dim uppercase">Edge CI Low</div>
                      <div className={cn(
                        'font-mono font-semibold text-sm',
                        (selectedBot.edge.ci_low_pct ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400',
                      )}>
                        {(selectedBot.edge.ci_low_pct ?? 0).toFixed(2)}%
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-dim uppercase">Trades OOS</div>
                      <div className="font-mono font-semibold text-sm">
                        {selectedBot.edge.n ?? 0}
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-dim uppercase">Worst Trade</div>
                      <div className="font-mono font-semibold text-sm text-red-400">
                        {(selectedBot.edge.worst_trade_pct ?? 0).toFixed(1)}%
                      </div>
                    </div>
                  </div>
                )}

                {/* Budget */}
                {selectedBot.budget && (
                  <div className="space-y-2">
                    <div className="flex justify-between text-xs">
                      <span className="text-muted">Budget alloué</span>
                      <span className="font-mono">{(selectedBot.budget.budget_pct * 100).toFixed(1)}%</span>
                    </div>
                    <div className="h-1.5 bg-card-hover rounded-full overflow-hidden">
                      <div
                        className="h-full bg-primary-400 rounded-full"
                        style={{ width: `${(selectedBot.budget.budget_pct * 100).toFixed(1)}%` }}
                      />
                    </div>
                    <div className="flex justify-between text-[10px] text-dim">
                      <span>Used: {((selectedBot.budget.used_pct ?? 0) * 100).toFixed(1)}%</span>
                    </div>
                  </div>
                )}

                {/* Actions contextuelles */}
                <div className="space-y-2 pt-2 border-t border-border">
                  <Button
                    variant={selectedBot.manual_active === false ? 'success' : 'danger'}
                    className="w-full"
                    onClick={() => handleForce(selectedBot.slot_key, selectedBot.manual_active === false)}
                    disabled={forceActive.isPending}
                  >
                    <Star className="w-4 h-4" />
                    {selectedBot.manual_active === false ? 'Forcer en actif' : 'Lever le forçage'}
                  </Button>
                  <div className="grid grid-cols-2 gap-2">
                    <Button
                      variant="outline"
                      onClick={() => handleForward(selectedBot.slot_key)}
                      disabled={runForward.isPending}
                    >
                      <RefreshCw className={cn('w-3 h-3', runForward.isPending && 'animate-spin')} />
                      Forward-test
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => handleReset(selectedBot.slot_key)}
                      disabled={resetSlot.isPending}
                    >
                      Reset CB
                    </Button>
                  </div>
                </div>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ── Card Bot v2 ───────────────────────────────────────────────────────────

interface BotCardV2Props {
  bot: Bot;
  onClick: () => void;
  onForce: () => void;
  onForward: () => void;
  forceLoading: boolean;
  forwardLoading: boolean;
}

function BotCardV2({ bot, onClick, onForce, onForward, forceLoading, forwardLoading }: BotCardV2Props) {
  const { strategy, tf, symbol } = parseSlotKey(bot.slot_key);
  const style = lifecycleStyle(bot.state);
  const confidence = getConfidence(bot);
  const budgetPct = bot.budget?.budget_pct || 0;
  const usedPct = bot.budget?.used_pct || 0;

  return (
    <Card
      className={cn('hover:border-border-hi transition-all cursor-pointer group', style.bg)}
      onClick={onClick}
      role="button"
      tabIndex={0}
      aria-label={`Bot ${strategy} ${tf} ${symbol || ''} — état ${style.label}`}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick();
        }
      }}
    >
      <CardContent className="space-y-2.5 p-3">
        {/* Header : strategy + tf + confidence */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span aria-label={`Confiance: ${confidence.level}`} title={`Confiance ${confidence.level}`}>
                {confidence.icon}
              </span>
              <span className="font-mono text-sm font-semibold truncate">{strategy}</span>
            </div>
            <div className="text-[11px] text-muted mt-0.5 font-mono">
              {tf}{symbol ? ` · ${symbol}` : ''}
            </div>
          </div>
          <Badge variant={
            bot.state === 'actif' ? 'success' :
            bot.state === 'essai' ? 'info' :
            bot.state === 'retire' ? 'danger' : 'warning'
          } className="text-[10px]">
            {style.icon} {style.label}
          </Badge>
        </div>

        {/* Budget bar */}
        {bot.budget && budgetPct > 0 && (
          <div>
            <div className="flex justify-between text-[10px] text-dim mb-0.5">
              <span>Budget {budgetPct.toFixed(1)}%</span>
              <span>Used {usedPct.toFixed(1)}%</span>
            </div>
            <div className="h-1 bg-card-hover rounded-full overflow-hidden">
              <div
                className="h-full bg-primary-400 rounded-full transition-all"
                style={{ width: `${Math.min((usedPct / Math.max(budgetPct, 1)) * 100, 100)}%` }}
              />
            </div>
          </div>
        )}

        {/* Edge summary */}
        {bot.edge && bot.edge.available && (
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-dim">Edge</span>
            <span className={cn('font-mono', (bot.edge.ci_low_pct ?? 0) > 0 ? 'text-emerald-400' : 'text-red-400')}>
              {(bot.edge.ci_low_pct ?? 0).toFixed(2)}%
            </span>
          </div>
        )}

        {/* Quick actions (arrêtent pas le clic sur la card) */}
        <div className="flex gap-1.5 pt-1.5 border-t border-border/50">
          <Button
            size="sm"
            variant="ghost"
            onClick={(e) => { e.stopPropagation(); onForce(); }}
            disabled={forceLoading}
            className="h-7 px-2 text-[10px] flex-1"
            aria-label={bot.manual_active === false ? 'Forcer en actif' : 'Lever le forçage'}
          >
            <Star className="w-3 h-3" />
            {bot.manual_active === false ? 'Forcer' : 'Libérer'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={(e) => { e.stopPropagation(); onForward(); }}
            disabled={forwardLoading}
            className="h-7 px-2 text-[10px]"
            aria-label="Relancer le forward-test"
          >
            <RefreshCw className={cn('w-3 h-3', forwardLoading && 'animate-spin')} />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
