'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronUp } from 'lucide-react';
import type { OptimizeSpaces } from '@/types';

export function ParamSpaceTable({ spaces }: { spaces: OptimizeSpaces }) {
  const [collapsed, setCollapsed] = useState(true);
  const entries = Object.entries(spaces || {});
  if (entries.length === 0) return null;
  return (
    <Card>
      <CardHeader
        className="cursor-pointer"
        onClick={() => setCollapsed((c) => !c)}
      >
        <CardTitle className="flex items-center gap-2">
          Espaces de paramètres
          <Badge variant="info">{entries.length} stratégies</Badge>
          <span className="ml-auto text-muted flex items-center gap-1 text-[10px] font-normal uppercase tracking-wider">
            {collapsed ? 'Déplier' : 'Masquer'}
            {collapsed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
          </span>
        </CardTitle>
      </CardHeader>
      {collapsed ? null : (
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-dim border-b border-border">
                <th className="p-3 font-medium">Stratégie</th>
                <th className="p-3 font-medium">Type</th>
                <th className="p-3 font-medium">Paramètres</th>
                <th className="p-3 font-medium text-right">Combinaisons</th>
                <th className="p-3 font-medium">Timeframes</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(([name, info]) => (
                <tr key={name} className="border-b border-border/30 hover:bg-card-hover">
                  <td className="p-3 font-mono font-semibold">{name}</td>
                  <td className="p-3">
                    <Badge variant={info.is_ml ? 'purple' : 'default'}>
                      {info.is_ml ? 'ML' : 'Classique'}
                    </Badge>
                  </td>
                  <td className="p-3 text-xs text-muted">
                    {info.params ? Object.keys(info.params).join(', ') : '—'}
                  </td>
                  <td className="p-3 text-right font-mono">{info.n_combos ?? '—'}</td>
                  <td className="p-3">
                    <div className="flex flex-wrap gap-1">
                      {(info.timeframes || []).map((tf) => (
                        <Badge key={tf} variant="default">{tf}</Badge>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
      )}
    </Card>
  );
}
