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
 *  - Filtre « forcés en actif » (bots force_active: true)
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
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import type { Bot } from '@/types';
import { isForcedActive } from '@/lib/schemas';

/** Extrait le TF d'un slot_key (ex. smart_money::4h::BTC/USDC → 4h). */
function botTimeframe(bot: Bot): string {
  const fromSlot = parseSlotKey(String(bot.slot_key || ''));
  if (fromSlot?.tf) return String(fromSlot.tf);
  return String((bot as any).timeframe || (bot as any).tf || '');
}

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
  // `force_active` (D6, cf. app/api/routes/portfolio.py) vaut `true` quand le
  // slot est FORCÉ en actif via `lifecycle.force_active` — `false` est donc
  // l'état normal de tout bot piloté par le cycle de vie automatique, pas un
  // « gel ». Le filtre d'origine (`showFrozen`) masquait par défaut tout bot
  // non forcé : sur un déploiement réel (240 candidats, aucun forçage) le
  // kanban s'affichait entièrement vide alors que l'en-tête annonçait
  // « 240 candidats ». On expose maintenant la sémantique réelle.
  // `isForcedActive` lit les deux noms le temps de la migration.
  const [onlyForced, setOnlyForced] = useState(false);
  const [edgePositiveOnly, setEdgePositiveOnly] = useState(false);
  const [tfFilter, setTfFilter] = useState<string>('all');
  /** Slot en cours de recalcul edge (spinner par carte). */
  const [forwardingSlot, setForwardingSlot] = useState<string | null>(null);

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

  // Filtres : état + TF unifié + edge positif + override manuel
  const filtered = useMemo(() => {
    let result = filter === 'all' ? bots : bots.filter((b) => b.state === filter);
    if (tfFilter !== 'all') {
      result = result.filter((b) => botTimeframe(b) === tfFilter);
    }
    if (edgePositiveOnly) {
      result = result.filter((b) => {
        const e = b.edge;
        if (!e || !e.available) return false;
        // Edge positif = borne basse CI > 0 (ou moyenne si pas de CI)
        const lo = e.ci_low_pct;
        if (lo != null && Number.isFinite(Number(lo))) return Number(lo) > 0;
        const mean = (e as any).mean_pct ?? (e as any).avg_return_pct;
        return mean != null && Number(mean) > 0;
      });
    }
    if (onlyForced) {
      result = result.filter((b) => isForcedActive(b));
    }
    return result;
  }, [bots, filter, tfFilter, edgePositiveOnly, onlyForced]);

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
    router.replace(`/bots?${params.toString()}`, { scroll: false });
  };

  const handleCloseDrawer = () => {
    setDrawerOpen(false);
    router.replace('/bots', { scroll: false });
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
    setForwardingSlot(slotKey);
    try {
      toast.info('Recalcul edge en cours…');
      const res: any = await runForward.mutateAsync(slotKey);
      const edge = res?.edge;
      const sim = res?.sim;
      const parts: string[] = [];
      if (edge?.available) {
        const lo = edge.ci_low_pct;
        const hi = edge.ci_high_pct;
        if (lo != null && hi != null) {
          parts.push(`Edge CI [${Number(lo).toFixed(2)}% ; ${Number(hi).toFixed(2)}%]`);
        } else if (lo != null) {
          parts.push(`Edge CI low ${Number(lo).toFixed(2)}%`);
        }
      }
      if (sim) {
        if (sim.total_trades != null) parts.push(`${sim.total_trades} trades`);
        if (sim.return_pct != null) parts.push(`ret ${Number(sim.return_pct).toFixed(2)}%`);
        if (sim.win_rate != null) parts.push(`WR ${(Number(sim.win_rate) * 100).toFixed(0)}%`);
      }
      if (res?.contract?.verdict) parts.push(String(res.contract.verdict));
      toast.success(
        parts.length
          ? `Edge actualisé — ${parts.join(' · ')}`
          : res?.ran
            ? 'Edge actualisé (carte rafraîchie)'
            : 'Recalcul terminé — pas de données edge',
        { duration: 8000 },
      );
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    } finally {
      setForwardingSlot(null);
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

      {/* Timeframe unifié + forçage manuel */}
      <div className="flex flex-wrap items-center gap-4 text-xs">
        <div className="flex items-center gap-2">
          <span className="text-dim">Timeframe</span>
          <button
            type="button"
            onClick={() => setTfFilter('all')}
            className={cn(
              'px-2 py-1 rounded-md text-[11px] font-mono border transition-colors',
              tfFilter === 'all'
                ? 'bg-primary-500/15 text-primary-400 border-primary-500/40'
                : 'bg-card-hover text-muted border-border hover:border-border-hi',
            )}
          >
            Tous
          </button>
          <TimeframeButtons
            value={tfFilter === 'all' ? '' : tfFilter}
            onChange={(tf) => setTfFilter(tf)}
            size="sm"
          />
        </div>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={edgePositiveOnly}
            onChange={(e) => setEdgePositiveOnly(e.target.checked)}
            className="rounded border-border"
          />
          <span className="text-muted">Edge positif uniquement (CI low &gt; 0)</span>
        </label>
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
                      onForce={() => handleForce(bot.slot_key, !isForcedActive(bot))}
                      onForward={() => handleForward(bot.slot_key)}
                      forceLoading={forceActive.isPending}
                      forwardLoading={forwardingSlot === bot.slot_key}
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
                  {isForcedActive(selectedBot) && (
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
                    variant={isForcedActive(selectedBot) ? 'danger' : 'success'}
                    className="w-full"
                    onClick={() => handleForce(selectedBot.slot_key, !isForcedActive(selectedBot))}
                    disabled={forceActive.isPending}
                  >
                    <Star className="w-4 h-4" />
                    {isForcedActive(selectedBot) ? 'Lever le forçage' : 'Forcer en actif'}
                  </Button>
                  <div className="grid grid-cols-2 gap-2">
                    <Button
                      variant="outline"
                      onClick={() => handleForward(selectedBot.slot_key)}
                      disabled={forwardingSlot === selectedBot.slot_key}
                    >
                      <RefreshCw className={cn(
                        'w-3 h-3',
                        forwardingSlot === selectedBot.slot_key && 'animate-spin',
                      )} />
                      {forwardingSlot === selectedBot.slot_key ? 'Calcul…' : 'Actu. edge'}
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
    /*
      La carte portait `role="button"` + tabIndex tout en contenant de vrais
      boutons d'action (« Forcer en actif », « Relancer le forward-test ») :
      axe le remonte en `nested-interactive` (serious) — un contrôle ne peut
      pas en contenir d'autres, et un lecteur d'écran ne sait plus quoi
      annoncer ni comment atteindre les actions internes.

      La carte redevient donc un simple conteneur ; c'est le NOM du bot qui
      devient le contrôle d'ouverture de la fiche. Le `onClick` de la carte est
      conservé pour le confort à la souris, mais il n'est plus le seul chemin :
      le bouton de nom offre l'équivalent clavier.
    */
    <Card
      className={cn('hover:border-border-hi transition-all cursor-pointer group', style.bg)}
      onClick={onClick}
    >
      <CardContent className="space-y-2.5 p-3">
        {/* Header : strategy + tf + confidence */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span aria-label={`Confiance: ${confidence.level}`} title={`Confiance ${confidence.level}`}>
                {confidence.icon}
              </span>
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); onClick(); }}
                aria-label={`Ouvrir la fiche du bot ${strategy} ${tf}${symbol ? ` ${symbol}` : ''} — état ${style.label}`}
                className="font-mono text-sm font-semibold truncate text-left hover:text-primary-400 focus:outline-none focus:ring-1 focus:ring-primary-400 rounded"
              >
                {strategy}
              </button>
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
            aria-label={isForcedActive(bot) ? 'Lever le forçage' : 'Forcer en actif'}
          >
            <Star className="w-3 h-3" />
            {isForcedActive(bot) ? 'Libérer' : 'Forcer'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={(e) => { e.stopPropagation(); onForward(); }}
            disabled={forwardLoading}
            className="h-7 px-2 text-[10px] flex-1"
            aria-label="Actualiser l'edge (recalcul stats)"
            title="Recalcule l'edge OOS et met à jour la carte"
          >
            <RefreshCw className={cn('w-3 h-3', forwardLoading && 'animate-spin')} />
            {forwardLoading ? 'Calcul…' : 'Actu. edge'}
          </Button>
        </div>
        {forwardLoading && (
          <div className="flex items-center gap-1.5 text-[10px] text-primary-400 pt-0.5">
            <RefreshCw className="w-3 h-3 animate-spin" />
            Recalcul edge en cours…
          </div>
        )}
      </CardContent>
    </Card>
  );
}
