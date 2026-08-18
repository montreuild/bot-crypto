'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn, formatDateTime } from '@/lib/utils';
import { formatPrice, type SmcZoneRow } from '@/components/views/smart-graph-helpers';

interface SmcTablesData {
  order_blocks?: SmcZoneRow[];
  fvgs?: SmcZoneRow[];
  liquidity_voids?: SmcZoneRow[];
  rejection_blocks?: SmcZoneRow[];
}

function ZoneTable({
  title,
  rows,
  empty,
  accent,
}: {
  title: string;
  rows: SmcZoneRow[];
  empty: string;
  accent: (kind?: string) => string;
}) {
  const showStart = title === 'Order Blocks' || title === 'FVG';
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <Badge variant="info">{rows.length}</Badge>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto max-h-80">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-card">
              <tr className="text-left text-dim border-b border-border">
                <th className="p-2 font-medium">Type</th>
                <th className="p-2 font-medium text-right">Top</th>
                <th className="p-2 font-medium text-right">Bottom</th>
                <th className="p-2 font-medium">Status</th>
                {showStart && <th className="p-2 font-medium">Start</th>}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 30).map((row, i) => (
                <tr key={`${row.kind}-${row.time_start}-${i}`} className="border-b border-border/30 hover:bg-card-hover">
                  <td className="p-2">
                    <span className={cn('font-semibold', accent(row.kind))}>
                      {row.kind === 'bullish' ? 'BULL' : 'BEAR'}
                    </span>
                  </td>
                  <td className="p-2 text-right font-mono">{formatPrice(row.top)}</td>
                  <td className="p-2 text-right font-mono">{formatPrice(row.bottom)}</td>
                  <td className="p-2">
                    {title === 'Order Blocks' ? (
                      <Badge variant={row.status === 'fresh' ? 'success' : row.status === 'touched' ? 'warning' : 'danger'}>
                        {row.status}
                      </Badge>
                    ) : (
                      <Badge variant="muted">{row.status}</Badge>
                    )}
                  </td>
                  {showStart && (
                    <td className="p-2 text-xs text-muted font-mono">{formatDateTime(row.time_start)}</td>
                  )}
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={showStart ? 5 : 4} className="p-4 text-center text-muted">{empty}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

export function SmartGraphTables({ data }: { data: SmcTablesData }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <ZoneTable
        title="Order Blocks"
        rows={data.order_blocks || []}
        empty="Aucun Order Block"
        accent={(k) => (k === 'bullish' ? 'text-emerald-400' : 'text-red-400')}
      />
      <ZoneTable
        title="FVG"
        rows={data.fvgs || []}
        empty="Aucun FVG"
        accent={(k) => (k === 'bullish' ? 'text-cyan-400' : 'text-amber-400')}
      />
      <ZoneTable
        title="Liquidity Voids"
        rows={data.liquidity_voids || []}
        empty="Aucun void"
        accent={() => 'text-purple-400'}
      />
      <ZoneTable
        title="Rejections"
        rows={data.rejection_blocks || []}
        empty="Aucune rejection"
        accent={(k) => (k === 'bullish' ? 'text-emerald-400' : 'text-rose-400')}
      />
    </div>
  );
}
