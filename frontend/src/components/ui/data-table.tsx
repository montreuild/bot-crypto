'use client';

/**
 * F1 — DataTable générique réutilisable.
 *
 * Factorisation (cf. audit frontend §3.3.1) : le dépôt compte 5 tables
 * spécialisées (trades-table, RealizedTradesTable, table inline /trades,
 * top-trials-table, strategy-comparison-table) qui dupliquent ~1500 lignes
 * de logique (tri, pagination, formatage, export CSV). Ce composant factorise
 * les patterns communs sans casser l'existant — il est conçu pour être
 * adopté progressivement par les tables spécialisées.
 *
 * Contract :
 *   <DataTable
 *     columns={columns}
 *     rows={rows}
 *     sortable
 *     paginated
 *     pageSize={20}
 *     onRowClick={(row) => ...}
 *     emptyLabel="Aucun trade"
 *   />
 *
 * Columns : chaque colonne déclare sa clé (pour le tri), son header, son
 * render (cell), et optionnellement son `sortValue` (si différent du rendu).
 *
 * Typage : generic <T> pour préserver le typage des rows côté consommateur.
 */

import { Fragment, useMemo, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronUp, ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';

export interface DataTableColumn<T> {
  /** Clé unique — utilisée comme key React ET pour le tri si `sortable`. */
  key: string;
  /** Libellé du header. */
  header: ReactNode;
  /** Rendu de la cellule. */
  render: (row: T, index: number) => ReactNode;
  /** Valeur extraite pour le tri (si non fournie, on tente `row[key]`). */
  sortValue?: (row: T) => string | number | null | undefined;
  /** Désactive le tri pour cette colonne (défaut : false). */
  noSort?: boolean;
  /** Alignement de la cellule (défaut : 'left'). */
  align?: 'left' | 'right' | 'center';
  /** Classes Tailwind additionnelles sur les `<td>` et `<th>`. */
  className?: string;
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[];
  /** Active le tri par colonne (défaut : true). */
  sortable?: boolean;
  /** Active la pagination (défaut : false). */
  paginated?: boolean;
  /** Taille de page si paginated (défaut : 20). */
  pageSize?: number;
  /** Colonne de tri initiale (key). */
  initialSortKey?: string;
  /** Sens du tri initial (défaut : false = descendant). */
  initialSortAsc?: boolean;
  /** Click sur une ligne. */
  onRowClick?: (row: T, index: number) => void;
  /** Message si rows vide (défaut : "Aucune donnée"). */
  emptyLabel?: string;
  /** Rend la ligne expandable (affiche un chevron + content au click). */
  expandable?: (row: T) => ReactNode;
  /** Key unique pour React (défaut : index). */
  rowKey?: (row: T, index: number) => string | number;
  /** Classes Tailwind additionnelles sur le `<table>`. */
  className?: string;
  /** Désactive le scroll horizontal (défaut : false = scroll auto). */
  noScrollX?: boolean;
}

export function DataTable<T>({
  columns,
  rows,
  sortable = true,
  paginated = false,
  pageSize = 20,
  initialSortKey,
  initialSortAsc = false,
  onRowClick,
  emptyLabel = 'Aucune donnée',
  expandable,
  rowKey,
  className = '',
  noScrollX = false,
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(initialSortKey ?? null);
  const [sortAsc, setSortAsc] = useState<boolean>(initialSortAsc);
  const [page, setPage] = useState(1);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  // Tri
  const sorted = useMemo(() => {
    if (!sortable || !sortKey) return rows;
    const col = columns.find((c) => c.key === sortKey);
    if (!col) return rows;
    const arr = [...rows];
    arr.sort((a, b) => {
      const av = col.sortValue ? col.sortValue(a) : (a as Record<string, unknown>)[sortKey];
      const bv = col.sortValue ? col.sortValue(b) : (b as Record<string, unknown>)[sortKey];
      let avn: string | number = av == null ? -Infinity : (av as string | number);
      let bvn: string | number = bv == null ? -Infinity : (bv as string | number);
      if (typeof avn === 'string') {
        avn = avn.toLowerCase();
        bvn = typeof bvn === 'string' ? bvn.toLowerCase() : String(bvn);
      }
      if (avn < bvn) return sortAsc ? -1 : 1;
      if (avn > bvn) return sortAsc ? 1 : -1;
      return 0;
    });
    return arr;
  }, [rows, sortKey, sortAsc, columns, sortable]);

  // Pagination
  const totalPages = paginated ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const currentPage = paginated ? Math.min(page, totalPages) : 1;
  const slice = paginated
    ? sorted.slice((currentPage - 1) * pageSize, currentPage * pageSize)
    : sorted;

  function toggleSort(colKey: string, noSort?: boolean) {
    if (!sortable || noSort) return;
    if (sortKey === colKey) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(colKey);
      setSortAsc(true);
    }
  }

  function getRowKey(row: T, index: number): string | number {
    if (rowKey) return rowKey(row, index);
    return index;
  }

  const alignClass = (a?: 'left' | 'right' | 'center') =>
    a === 'right' ? 'text-right' : a === 'center' ? 'text-center' : 'text-left';

  if (rows.length === 0) {
    return (
      <div className="text-center text-muted-foreground text-sm py-8 italic">
        {emptyLabel}
      </div>
    );
  }

  return (
    <div className={noScrollX ? '' : 'overflow-x-auto'}>
      <table className={`w-full text-xs ${className}`}>
        <thead>
          <tr className="border-b border-border text-muted-foreground">
            {expandable && <th className="py-1 px-2 w-8" />}
            {columns.map((col) => {
              const isSorted = sortKey === col.key;
              const canSort = sortable && !col.noSort;
              // Le tri passe par un vrai <button> et non par un onClick posé
              // sur le <th> : un <th> cliquable n'est ni focusable ni activable
              // au clavier, et aucune technologie d'assistance ne l'annonce
              // comme actionnable. C'est le pattern qu'utilisaient déjà les
              // tables du dépôt avant leur migration ici.
              // `aria-sort="none"` (et non `undefined`) sur une colonne triable
              // mais inactive : c'est ce qui signale QU'ELLE est triable.
              const label = (
                <span className="inline-flex items-center gap-1">
                  {col.header}
                  {canSort && isSorted && (
                    <span className="text-cyan-400">
                      {sortAsc ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                    </span>
                  )}
                </span>
              );
              return (
                <th
                  key={col.key}
                  scope="col"
                  className={`py-1 px-2 ${alignClass(col.align)} ${col.className ?? ''}`}
                  aria-sort={
                    canSort
                      ? (isSorted ? (sortAsc ? 'ascending' : 'descending') : 'none')
                      : undefined
                  }
                >
                  {canSort ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(col.key, col.noSort)}
                      className="inline-flex items-center gap-1 select-none hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-cyan-500/60 rounded"
                    >
                      {label}
                    </button>
                  ) : (
                    label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {slice.map((row, idx) => {
            const realIdx = (currentPage - 1) * pageSize + idx;
            const key = getRowKey(row, realIdx);
            const isExpanded = expandedIdx === realIdx;
            return (
              <Fragment key={key}>
                <tr
                  className={`border-b border-border/50 hover:bg-card-hover/50 ${
                    onRowClick ? 'cursor-pointer' : ''
                  } ${isExpanded ? 'bg-card-hover/30' : ''}`}
                  onClick={() => {
                    if (expandable) {
                      setExpandedIdx(isExpanded ? null : realIdx);
                    }
                    if (onRowClick) {
                      onRowClick(row, realIdx);
                    }
                  }}
                >
                  {expandable && (
                    <td className="py-1 px-2 text-muted-foreground">
                      {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                    </td>
                  )}
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={`py-1 px-2 ${alignClass(col.align)} ${col.className ?? ''}`}
                    >
                      {col.render(row, realIdx)}
                    </td>
                  ))}
                </tr>
                {expandable && isExpanded && (
                  <tr className="bg-card-hover/20">
                    <td colSpan={columns.length + 1} className="py-2 px-4">
                      {expandable(row)}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>

      {paginated && totalPages > 1 && (
        <div className="flex items-center justify-between mt-2 text-xs text-muted-foreground">
          <span>
            {sorted.length} ligne(s) — page {currentPage}/{totalPages}
          </span>
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage(Math.max(1, currentPage - 1))}
              disabled={currentPage <= 1}
              className="h-7 px-2"
            >
              <ChevronLeft className="w-3 h-3" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage(Math.min(totalPages, currentPage + 1))}
              disabled={currentPage >= totalPages}
              className="h-7 px-2"
            >
              <ChevronRight className="w-3 h-3" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
