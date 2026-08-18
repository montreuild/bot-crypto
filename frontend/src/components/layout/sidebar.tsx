'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import { useI18n } from '@/lib/i18n';
import { useWebSocket } from '@/lib/ws-provider';
import {
  Bot, Settings, Activity,
  Zap, Database, Network, Sparkles,
  ClipboardList, Wallet, Cpu,
  ScrollText, Archive,
} from 'lucide-react';

const NAV_GROUPS = [
  {
    labelKey: 'nav.trading',
    items: [
      { href: '/portfolio', labelKey: 'nav.portfolio', icon: Wallet },
      { href: '/bots', labelKey: 'nav.bots', icon: Bot },
      { href: '/trades', labelKey: 'nav.trades', icon: Activity },
    ],
  },
  {
    labelKey: 'nav.research',
    items: [
      { href: '/lab', labelKey: 'nav.lab', icon: Sparkles },
      { href: '/market', labelKey: 'nav.market', icon: Network },
      { href: '/audit', labelKey: 'nav.audit', icon: ClipboardList },
      { href: '/audit-log', labelKey: 'nav.audit_log', icon: ScrollText },
    ],
  },
  {
    labelKey: 'nav.data',
    items: [
      { href: '/data', labelKey: 'nav.data_ohlcv', icon: Database },
      { href: '/models', labelKey: 'nav.ml', icon: Archive },
    ],
  },
  {
    labelKey: 'nav.config',
    items: [
      { href: '/settings', labelKey: 'nav.settings', icon: Settings },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { t } = useI18n();
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
          <div key={group.labelKey} className="mb-6">
            <div className="px-3 mb-2 text-[10px] font-semibold uppercase tracking-wider text-dim">
              {t(group.labelKey)}
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
                    <span>{t(item.labelKey)}</span>
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
