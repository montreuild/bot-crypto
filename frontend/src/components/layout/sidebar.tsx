'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import {
  LayoutDashboard, Bot, LineChart, Settings, Activity,
  Zap, AlertCircle, Database, Network,
} from 'lucide-react';

const NAV_GROUPS = [
  {
    label: 'Trading',
    items: [
      { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
      { href: '/bots', label: 'Mes Bots', icon: Bot },
      { href: '/trades', label: 'Trades', icon: Activity },
    ],
  },
  {
    label: 'Recherche',
    items: [
      { href: '/backtest', label: 'Backtest', icon: LineChart },
      { href: '/scanner', label: 'Scanner', icon: Network },
    ],
  },
  {
    label: 'Configuration',
    items: [
      { href: '/config', label: 'Configuration', icon: Settings },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();

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

      {/* Footer */}
      <div className="border-t border-border p-4">
        <div className="text-[10px] text-dim font-mono">
          <div className="flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            Connected
          </div>
        </div>
      </div>
    </aside>
  );
}
