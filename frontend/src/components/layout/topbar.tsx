'use client';

import { useBotStatus, useHealth } from '@/hooks/use-api';
import { useWebSocket } from '@/lib/ws-provider';
import { cn, formatUSD, getStoredTheme, setStoredTheme } from '@/lib/utils';
import { useRouter } from 'next/navigation';
import {
  Play, Square, RefreshCw, AlertTriangle, Wifi, WifiOff, Loader2,
  Sun, Moon, Search, Command,
} from 'lucide-react';
import { useStartBot, useStopBot, useResetHalt } from '@/hooks/use-api';
import { toast } from 'sonner';
import { useMemo, useState, useEffect } from 'react';

export function Topbar() {
  const { data: status } = useBotStatus();
  const { data: health } = useHealth();
  const { status: wsStatus } = useWebSocket();
  const startBot = useStartBot();
  const stopBot = useStopBot();
  const resetHalt = useResetHalt();
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [searchOpen, setSearchOpen] = useState(false);

  // Initialise le thème au mount
  useEffect(() => {
    setTheme(getStoredTheme());
  }, []);

  // Raccourci clavier Cmd/Ctrl+K pour la recherche
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // `toLowerCase()` : avec CapsLock (ou Shift) `e.key` vaut 'K', et le
      // raccourci ne se déclenchait pas.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setSearchOpen((v) => !v);
      }
      if (e.key === 'Escape') setSearchOpen(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    setStoredTheme(next);
    setTheme(next);
  };

  const isRunning = status?.status === 'running';
  const isPaperMode = status?.paper_mode ?? true;
  const pnlPct = status?.total_pnl_pct ?? 0;
  const cbActive = status?.circuit_breaker_active ?? false;

  const onToggleBot = async () => {
    try {
      if (isRunning) {
        await stopBot.mutateAsync(false);
        toast.success('Bot arrêté');
      } else {
        await startBot.mutateAsync();
        toast.success('Bot démarré');
      }
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  const onResetHalt = async () => {
    try {
      await resetHalt.mutateAsync(false);
      toast.success('Circuit breaker réinitialisé');
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    }
  };

  // PnL color
  const pnlColorClass = useMemo(() => {
    if (pnlPct > 0) return 'text-emerald-400';
    if (pnlPct < 0) return 'text-red-400';
    return 'text-muted';
  }, [pnlPct]);

  return (
    <header className="h-16 bg-surface border-b border-border flex items-center px-6 gap-4 flex-shrink-0">
      {/* Bot control */}
      <button
        onClick={onToggleBot}
        disabled={startBot.isPending || stopBot.isPending}
        className={cn(
          'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all',
          isRunning
            ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20 border border-red-500/30'
            : 'bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 border border-emerald-500/30',
          (startBot.isPending || stopBot.isPending) && 'opacity-50 cursor-not-allowed',
        )}
      >
        {(startBot.isPending || stopBot.isPending) ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : isRunning ? (
          <Square className="w-4 h-4" fill="currentColor" />
        ) : (
          <Play className="w-4 h-4" fill="currentColor" />
        )}
        {isRunning ? 'Arrêter' : 'Démarrer'}
      </button>

      {/* Mode badge */}
      <div className={cn(
        'px-3 py-1.5 rounded-full text-xs font-semibold border',
        isPaperMode
          ? 'bg-amber-500/10 text-amber-400 border-amber-500/30'
          : 'bg-red-500/10 text-red-400 border-red-500/30 animate-pulse',
      )}>
        {isPaperMode ? 'PAPER' : 'LIVE'}
      </div>

      {/* Status indicator */}
      <div className="flex items-center gap-2 text-sm">
        <span className={cn(
          'w-2 h-2 rounded-full',
          isRunning ? 'bg-emerald-400 animate-pulse' : 'bg-dim',
        )} />
        <span className="text-muted">{isRunning ? 'Running' : 'Stopped'}</span>
        {status?.cycle !== undefined && (
          <span className="text-dim font-mono text-xs">· cycle #{status.cycle}</span>
        )}
      </div>

      {/* Circuit breaker */}
      {cbActive && (
        <button
          onClick={onResetHalt}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-red-500/10 text-red-400 border border-red-500/30 text-xs font-semibold animate-pulse hover:bg-red-500/20"
        >
          <AlertTriangle className="w-3.5 h-3.5" />
          CB Active — Reset
        </button>
      )}

      <div className="flex-1" />

      {/* Capital & PnL */}
      {status?.capital !== undefined && (
        <div className="flex items-center gap-6">
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-wider text-dim">Capital</div>
            <div className="font-mono font-semibold text-sm">{formatUSD(status.capital)}</div>
          </div>
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-wider text-dim">PnL Total</div>
            <div className={cn('font-mono font-semibold text-sm', pnlColorClass)}>
              {pnlPct > 0 ? '+' : ''}{pnlPct.toFixed(2)}%
            </div>
          </div>
        </div>
      )}

      {/* WS status */}
      <div className="flex items-center gap-2 ml-2">
        {wsStatus === 'connected' ? (
          <Wifi className="w-4 h-4 text-emerald-400" />
        ) : wsStatus === 'connecting' ? (
          <Loader2 className="w-4 h-4 text-amber-400 animate-spin" />
        ) : (
          <WifiOff className="w-4 h-4 text-red-400" />
        )}
      </div>

      {/* Health */}
      {health && (
        <div className="flex items-center gap-1 text-xs">
          {health.db && <span title="DB OK" className="w-2 h-2 rounded-full bg-emerald-400" />}
          {health.exchange && <span title="Exchange OK" className="w-2 h-2 rounded-full bg-emerald-400" />}
          {health.trader && <span title="Trader OK" className="w-2 h-2 rounded-full bg-emerald-400" />}
        </div>
      )}

      {/* Recherche cmd+K */}
      <button
        onClick={() => setSearchOpen(true)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-card-hover border border-border text-xs text-muted hover:text-foreground hover:border-border-hi transition-all"
        title="Recherche (Cmd+K)"
      >
        <Search className="w-3.5 h-3.5" />
        <span className="hidden md:inline">Rechercher</span>
        <kbd className="hidden md:inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-background text-[10px] font-mono">
          <Command className="w-2.5 h-2.5" />K
        </kbd>
      </button>

      {/* Theme toggle */}
      <button
        onClick={toggleTheme}
        className="p-2 rounded-lg hover:bg-card-hover text-muted hover:text-foreground transition-colors"
        title={theme === 'dark' ? 'Passer en mode clair' : 'Passer en mode sombre'}
      >
        {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
      </button>

      {/* Search modal */}
      {searchOpen && <SearchModal onClose={() => setSearchOpen(false)} />}
    </header>
  );
}

// ── Search modal (cmd+K) ────────────────────────────────────────────────────

const SEARCH_PAGES = [
  { href: '/dashboard', label: 'Dashboard', desc: 'Vue temps réel du trading', keywords: ['live', 'trading', 'positions', 'pnl'] },
  { href: '/bots', label: 'Mes Bots', desc: 'Portefeuille de stratégies', keywords: ['stratégies', 'lifecycle', 'candidat', 'essai', 'actif'] },
  { href: '/trades', label: 'Trades', desc: 'Historique des trades', keywords: ['historique', 'export', 'csv'] },
  { href: '/portfolio', label: 'Portefeuille', desc: 'Vue fonds détaillée', keywords: ['allocation', 'shadow', 'lifecycle', 'risk'] },
  { href: '/backtest', label: 'Backtest', desc: 'Test de stratégies sur données historiques', keywords: ['walk-forward', 'monte-carlo', 'test'] },
  { href: '/scanner', label: 'Scanner', desc: 'Fast Analyse SMC/ICT', keywords: ['patterns', 'smc', 'ict', 'fvg', 'ob'] },
  { href: '/smartgraph', label: 'Smart Graph', desc: 'Analyse SMC avancée', keywords: ['smc', 'order block', 'liquidity', 'fvg', 'bos', 'choch'] },
  { href: '/smartreplay', label: 'Smart Replay', desc: 'Rejeu bougie par bougie', keywords: ['rejeu', 'smc', 'barre', 'slider', 'play'] },
  { href: '/compare', label: 'Comparatif', desc: 'Comparaison multi-stratégies', keywords: ['comparaison', 'multi', 'stratégies', 'côte à côte'] },
  { href: '/replay', label: 'Replay', desc: 'Rejeu multi-timeframe', keywords: ['rejeu', 'multi-tf', 'validation'] },
  { href: '/optimizer', label: 'Optimiseur', desc: 'Optimisation des paramètres', keywords: ['bayesian', 'grid', 'random', 'trials'] },
  { href: '/audit', label: 'Audit OOS', desc: 'Résultats OOS optimiseur', keywords: ['oos', 'résultats', 'score'] },
  { href: '/audit-log', label: 'Journal Audit', desc: 'Traçabilité des actions sensibles', keywords: ['audit', 'log', 'actions', 'sécurité'] },
  { href: '/derivatives', label: 'Dérivés', desc: 'Funding, OI, LSR, taker', keywords: ['funding', 'open interest', 'long short'] },
  { href: '/data', label: 'Données', desc: 'Cache bougies OHLCV', keywords: ['ohlcv', 'candles', 'cache', 'parquet'] },
  { href: '/ml', label: 'ML', desc: 'Modèles Machine Learning', keywords: ['machine', 'learning', 'lightgbm', 'random forest'] },
  { href: '/models', label: 'Registre modèles', desc: 'Registre versionné, gate de promotion, entraînement', keywords: ['registre', 'version', 'gate', 'pin', 'entraînement', 'sweep', 'promotion'] },
  { href: '/config', label: 'Configuration', desc: 'Stratégies, risk, notifications', keywords: ['paramètres', 'risk', 'exchange'] },
  { href: '/settings', label: 'Réglages', desc: 'Presets, thème, expert mode', keywords: ['preset', 'thème', 'clair', 'sombre'] },
];

function SearchModal({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [selectedIdx, setSelectedIdx] = useState(0);
  const router = useRouter();

  const filtered = SEARCH_PAGES.filter((p) => {
    if (!query) return true;
    const q = query.toLowerCase();
    return p.label.toLowerCase().includes(q)
      || p.desc.toLowerCase().includes(q)
      || p.keywords.some((k) => k.includes(q));
  });

  const onNavigate = (href: string) => {
    router.push(href);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-24 bg-black/50 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl mx-4 rounded-xl border border-border bg-card shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <Search className="w-4 h-4 text-dim" />
          <input
            autoFocus
            type="text"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setSelectedIdx(0); }}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                setSelectedIdx((i) => Math.min(i + 1, filtered.length - 1));
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setSelectedIdx((i) => Math.max(i - 1, 0));
              } else if (e.key === 'Enter' && filtered[selectedIdx]) {
                onNavigate(filtered[selectedIdx].href);
              }
            }}
            placeholder="Rechercher une page, une action..."
            className="flex-1 bg-transparent outline-none text-sm placeholder:text-dim"
          />
          <kbd className="px-1.5 py-0.5 rounded bg-background text-[10px] font-mono text-dim">ESC</kbd>
        </div>
        <div className="max-h-80 overflow-y-auto p-2">
          {filtered.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-muted">Aucun résultat</div>
          ) : (
            filtered.map((page, idx) => (
              <button
                key={page.href}
                onClick={() => onNavigate(page.href)}
                onMouseEnter={() => setSelectedIdx(idx)}
                className={cn(
                  'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors',
                  idx === selectedIdx ? 'bg-primary-500/10 text-primary-400' : 'hover:bg-card-hover',
                )}
              >
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm">{page.label}</div>
                  <div className="text-xs text-muted truncate">{page.desc}</div>
                </div>
                <kbd className="px-1.5 py-0.5 rounded bg-background text-[10px] font-mono text-dim">
                  ↵
                </kbd>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
