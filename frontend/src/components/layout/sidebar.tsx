'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import { useWebSocket } from '@/lib/ws-provider';
import {
  LayoutDashboard, Bot, LineChart, Settings, Activity,
  Zap, Database, Network, Sparkles, Repeat,
  BrainCircuit, ClipboardList, TrendingUp, Wallet, Cpu,
  CandlestickChart, Film, GitCompare, ScrollText, Archive,
} from 'lucide-react';

const NAV_GROUPS = [
  {
    label: 'Trading',
    items: [
      { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
      { href: '/portfolio-v2', label: 'Portefeuille v2', icon: Wallet },
      { href: '/bots-v2', label: 'Mes Bots v2', icon: Bot },
      { href: '/bots', label: 'Mes Bots (ancien)', icon: Bot },
      { href: '/trades', label: 'Trades', icon: Activity },
      { href: '/portfolio', label: 'Portefeuille (ancien)', icon: Wallet },
    ],
  },
  {
    label: 'Recherche',
    items: [
      { href: '/lab', label: 'Laboratoire', icon: Sparkles },
      { href: '/market', label: 'Marché', icon: Network },
      { href: '/backtest', label: 'Backtest', icon: LineChart },
      { href: '/scanner', label: 'Scanner', icon: Network },
      { href: '/replay', label: 'Replay', icon: Repeat },
      { href: '/smartgraph', label: 'Smart Graph', icon: CandlestickChart },
      { href: '/smartreplay', label: 'Smart Replay', icon: Film },
      { href: '/compare', label: 'Comparatif', icon: GitCompare },
      { href: '/optimizer', label: 'Optimiseur', icon: Sparkles },
      { href: '/audit', label: 'Audit OOS', icon: ClipboardList },
      { href: '/audit-log', label: 'Journal Audit', icon: ScrollText },
    ],
  },
  {
    label: 'Données',
    items: [
      { href: '/derivatives', label: 'Dérivées', icon: TrendingUp },
      { href: '/data', label: 'Bougies OHLCV', icon: Database },
      { href: '/ml', label: 'Modèles ML', icon: BrainCircuit },
      { href: '/models', label: 'Registre modèles', icon: Archive },
    ],
  },
  {
    label: 'Configuration',
    items: [
      { href: '/settings-v2', label: 'Réglages v2', icon: Cpu },
      { href: '/config', label: 'Configuration', icon: Settings },
      { href: '/settings', label: 'Réglages (ancien)', icon: Cpu },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  // S0-F1-US5 — Le footer "Connected" était hardcodé et mentait quand le
  // backend était down. On consomme maintenant l'état réel du WS.
  const { status: wsStatus } = useWebSocket();

  const wsLabel = wsStatus === 'connected' ? 'Connected'
    : wsStatus === 'connecting' ? 'Connecting...'
    : wsStatus === 'error' ? 'Error'
    : 'Disconnected';
  const wsColor = wsStatus === 'connected' ? 'bg-emerald-400'
    : wsStatus === 'connecting' ? 'bg-amber-400 animate-pulse'
    : 'bg-red-400';

  return (
    <aside className="w-60 flex-shrink-0 bg-surface border-r border-border flex flex-col">
      {/* Logo */}
      <div className="h-16 flex items-center gap-3 px-5 border-b border-border">
        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-primary-400 to-purple-600 flex items-center justify-center glow-cyan">
          <Zap className="w-5 h-5 text-white" fill="white" />
        </div>
        <div>
          <div className="font-bold text-base leading-tight">Crypto Bot</div>
          <div className="text-[10px] text-dim font-mono">v12.17 · live</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-4 px-3">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-6">
            <div className="px-3 mb-2 text-[10px] font-semibold uppercase tracking-wider text-dim">
              {group.label}
            </div>
            <div className="space-y-1">
              {group.items.map((item) => {
                const Icon = item.icon;
                const active = pathname === item.href || pathname?.startsWith(item.href + '/');
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      'flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all',
                      active
                        ? 'bg-primary-500/10 text-primary-400 border-l-2 border-primary-400'
                        : 'text-muted hover:text-foreground hover:bg-card-hover',
                    )}
                  >
                    <Icon className="w-4 h-4 flex-shrink-0" />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer — état réel du WS */}
      <div className="border-t border-border p-4">
        <div className="text-[10px] text-dim font-mono">
          <div className="flex items-center gap-2" aria-label={`WebSocket: ${wsLabel}`}>
            <span className={cn('w-1.5 h-1.5 rounded-full', wsColor)} />
            {wsLabel}
          </div>
        </div>
      </div>
    </aside>
  );
}
