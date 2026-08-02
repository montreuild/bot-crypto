'use client';

/**
 * Journal de notifications — `GET /api/notifications`.
 *
 * Lot Portefeuille : ce fil vivait dans `/portfolio` et était la première des
 * deux raisons pour lesquelles sa 308 vers `/portfolio` restait bloquée.
 *
 * ⚠ À ne pas confondre avec `LiveTradesFeed`, également monté sur
 * `/portfolio` : celui-ci lit le flux WebSocket des trades, celui-là
 * l'historique persisté des notifications (halt, kill switch, drawdown), avec
 * ses niveaux info / warning / critical. Les deux se complètent.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn, timeAgo, formatDateTime } from '@/lib/utils';
import { useNotifications } from '@/hooks/use-api';
import { AlertCircle, Bell, ShieldAlert } from 'lucide-react';

const LEVEL_VARIANT: Record<string, 'info' | 'warning' | 'danger' | 'default'> = {
  info: 'info',
  warning: 'warning',
  critical: 'danger',
};

const LEVEL_COLOR: Record<string, string> = {
  info: 'text-cyan-400',
  warning: 'text-amber-400',
  critical: 'text-red-400',
};

export function ActivityFeedList({ items }: { items: any[] }) {
  if (!items || items.length === 0) {
    return <div className="text-sm text-muted text-center py-6">Aucune activité récente</div>;
  }
  return (
    <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
      {items.slice(0, 30).map((n, i) => {
        const level = n.level || 'info';
        return (
          <div
            key={i}
            className="flex items-start gap-3 p-3 rounded-lg bg-card-hover border border-border"
          >
            <div className={cn('mt-0.5', LEVEL_COLOR[level] || 'text-muted')}>
              {level === 'critical' ? (
                <ShieldAlert className="w-4 h-4" />
              ) : level === 'warning' ? (
                <AlertCircle className="w-4 h-4" />
              ) : (
                <Bell className="w-4 h-4" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold truncate">
                  {n.title || n.message?.slice(0, 60) || 'Notification'}
                </span>
                <Badge variant={LEVEL_VARIANT[level] || 'default'}>{level}</Badge>
              </div>
              {n.message && (
                <div className="text-xs text-muted mt-0.5 break-words">{n.message}</div>
              )}
              <div className="text-[10px] text-dim font-mono mt-1">
                {n.ts ? timeAgo(n.ts) : ''}
                {n.ts && <span className="ml-2">· {formatDateTime(n.ts)}</span>}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Carte autonome : fait sa propre requête. */
export function ActivityFeed({ fallbackItems }: { fallbackItems?: any[] }) {
  const { data } = useNotifications(30, 'info');
  return (
    <Card>
      <CardHeader>
        <CardTitle>Activité récente</CardTitle>
        <Bell className="w-4 h-4 text-primary-400" />
      </CardHeader>
      <CardContent>
        <ActivityFeedList items={data?.notifications || fallbackItems || []} />
      </CardContent>
    </Card>
  );
}
