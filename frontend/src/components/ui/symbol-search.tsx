'use client';

/**
 * Champ symbole : saisie libre + liste filtrée auto-découverte
 * (cache OHLCV + univers crypto/actions).
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import { ChevronsUpDown } from 'lucide-react';

export type SymbolGroup = 'crypto' | 'equity' | 'other';

export interface SymbolOption {
  symbol: string;
  group: SymbolGroup;
  hint?: string;
}

const GROUP_LABEL: Record<SymbolGroup, string> = {
  crypto: 'Crypto',
  equity: 'Actions',
  other: 'Autres',
};

const GROUP_ORDER: SymbolGroup[] = ['crypto', 'equity', 'other'];

export function classifySymbol(symbol: string): SymbolGroup {
  const s = symbol.trim();
  if (s.includes('/')) return 'crypto';
  if (/\.[A-Za-z]{1,4}$/.test(s)) return 'equity';
  return 'other';
}

export function collectSymbolOptions(args: {
  datasets?: Array<{ symbol?: string }>;
  universeMembers?: Array<{ symbol?: string; name?: string; universe?: string }>;
}): SymbolOption[] {
  const map = new Map<string, SymbolOption>();
  const add = (raw: string, hint?: string) => {
    const symbol = raw.trim();
    if (!symbol) return;
    const prev = map.get(symbol);
    if (!prev) {
      map.set(symbol, { symbol, group: classifySymbol(symbol), hint });
      return;
    }
    if (!prev.hint && hint) map.set(symbol, { ...prev, hint });
  };
  for (const d of args.datasets || []) add(d.symbol || '');
  for (const m of args.universeMembers || []) {
    add(m.symbol || '', m.name || m.universe);
  }
  return [...map.values()].sort((a, b) => a.symbol.localeCompare(b.symbol));
}

function flattenDatasets(data: unknown): Array<{ symbol?: string }> {
  if (!data || typeof data !== 'object') return [];
  const payload = data as {
    datasets?: Array<{ symbol?: string }>;
    store?: Record<string, unknown>;
  };
  if (Array.isArray(payload.datasets)) return payload.datasets;
  if (payload.store && typeof payload.store === 'object') {
    return Object.keys(payload.store).map((symbol) => ({ symbol }));
  }
  if (Array.isArray(data)) return data as Array<{ symbol?: string }>;
  return [];
}

export function SymbolSearchInput({
  value,
  onChange,
  id = 'symbol-search',
  className,
}: {
  value: string;
  onChange: (symbol: string) => void;
  id?: string;
  className?: string;
}) {
  const [text, setText] = useState(value);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => { setText(value); }, [value]);

  const { data: options = [] } = useQuery({
    queryKey: ['discoveredSymbols'],
    queryFn: async (): Promise<SymbolOption[]> => {
      const [status, universes] = await Promise.all([
        api.getDataStatus().catch(() => null),
        api.getUniverses().catch(() => ({ universes: [] as Array<{ id?: string }> })),
      ]);
      const uniList = universes?.universes ?? [];
      const membersNested = await Promise.all(
        uniList.map(async (u) => {
          if (!u.id) return [];
          try {
            const detail = await api.getUniverse(u.id);
            return (detail.members || []).map((m) => ({
              symbol: m.symbol,
              name: m.name,
              universe: u.id,
            }));
          } catch {
            return [];
          }
        }),
      );
      return collectSymbolOptions({
        datasets: flattenDatasets(status),
        universeMembers: membersNested.flat(),
      });
    },
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  const filtered = useMemo(() => {
    const q = text.trim().toLowerCase();
    if (!q) return options.slice(0, 40);
    return options.filter((o) => {
      const hay = `${o.symbol} ${o.hint || ''}`.toLowerCase();
      return hay.includes(q);
    }).slice(0, 40);
  }, [options, text]);

  const exact = filtered.some((o) => o.symbol.toLowerCase() === text.trim().toLowerCase());
  const showCustom = text.trim() !== '' && !exact;

  const items = useMemo(() => {
    const rows: Array<{ symbol: string; hint?: string; custom?: boolean; group?: SymbolGroup }> = [
      ...(showCustom ? [{ symbol: text.trim(), custom: true as const }] : []),
      ...filtered,
    ];
    return rows;
  }, [filtered, showCustom, text]);

  useEffect(() => { setHi(0); }, [text, open]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const commit = (symbol: string) => {
    const next = symbol.trim();
    if (!next) return;
    setText(next);
    if (next !== value) onChange(next);
    setOpen(false);
  };

  const grouped = useMemo(() => {
    const out: Array<{ label?: string; rows: typeof items }> = [];
    if (showCustom) {
      out.push({ rows: items.filter((i) => i.custom) });
    }
    for (const g of GROUP_ORDER) {
      const rows = items.filter((i) => !i.custom && i.group === g);
      if (rows.length) out.push({ label: GROUP_LABEL[g], rows });
    }
    return out;
  }, [items, showCustom]);

  return (
    <div ref={rootRef} className={cn('relative', className)}>
      <div className="relative">
        <input
          id={id}
          aria-label="Symbole"
          aria-expanded={open}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          role="combobox"
          value={text}
          onChange={(e) => { setText(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            // Laisse le click liste se faire avant de fermer.
            window.setTimeout(() => {
              if (!rootRef.current?.contains(document.activeElement)) {
                commit(text);
              }
            }, 120);
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setOpen(true);
              setHi((i) => Math.min(items.length - 1, i + 1));
            } else if (e.key === 'ArrowUp') {
              e.preventDefault();
              setHi((i) => Math.max(0, i - 1));
            } else if (e.key === 'Enter') {
              e.preventDefault();
              const pick = items[hi] || items[0];
              commit(pick ? pick.symbol : text);
            } else if (e.key === 'Escape') {
              setOpen(false);
              setText(value);
            }
          }}
          className="w-56 px-3 py-2 pr-8 bg-card-hover border border-border rounded-md text-sm font-mono"
          placeholder="BTC/USDC ou AIR.PA"
          autoComplete="off"
          spellCheck={false}
        />
        <ChevronsUpDown className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-dim" />
      </div>
      {open && (
        <ul
          ref={listRef}
          id={`${id}-list`}
          role="listbox"
          className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-md border border-border bg-card shadow-lg py-1"
        >
          {items.length === 0 ? (
            <li className="px-3 py-2 text-xs text-muted">Aucun symbole — saisissez une valeur libre</li>
          ) : (
            grouped.map((g, gi) => (
              <li key={g.label || `custom-${gi}`}>
                {g.label && (
                  <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-dim">{g.label}</div>
                )}
                <ul>
                  {g.rows.map((row) => {
                    const idx = items.indexOf(row);
                    const active = idx === hi;
                    return (
                      <li key={`${row.custom ? 'c' : 's'}-${row.symbol}`}>
                        <button
                          type="button"
                          role="option"
                          aria-selected={active}
                          className={cn(
                            'w-full text-left px-3 py-1.5 text-sm font-mono',
                            active ? 'bg-card-hover text-foreground' : 'text-muted hover:bg-card-hover hover:text-foreground',
                          )}
                          onMouseDown={(e) => e.preventDefault()}
                          onMouseEnter={() => setHi(idx)}
                          onClick={() => commit(row.symbol)}
                        >
                          {row.custom ? (
                            <span>Utiliser « {row.symbol} »</span>
                          ) : (
                            <span className="flex items-center justify-between gap-2">
                              <span>{row.symbol}</span>
                              {row.hint && <span className="text-[10px] text-dim font-sans truncate">{row.hint}</span>}
                            </span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
