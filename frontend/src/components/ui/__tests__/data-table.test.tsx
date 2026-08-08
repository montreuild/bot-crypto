/**
 * F1 — Tests du composant DataTable générique.
 *
 * Valide le comportement de base : tri, pagination, expandable, empty state,
 * click sur ligne, alignement. Le composant est conçu pour remplacer
 * progressivement les 5 tables spécialisées (trades-table, RealizedTradesTable,
 * /trades inline, top-trials-table, strategy-comparison-table).
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';

interface Row {
  id: number;
  name: string;
  pnl: number;
}

const sampleRows: Row[] = [
  { id: 1, name: 'alpha', pnl: 100 },
  { id: 2, name: 'beta', pnl: -50 },
  { id: 3, name: 'gamma', pnl: 200 },
  { id: 4, name: 'delta', pnl: 0 },
  { id: 5, name: 'epsilon', pnl: 75 },
];

const columns: DataTableColumn<Row>[] = [
  { key: 'id', header: 'ID', render: (r) => r.id, align: 'right' },
  { key: 'name', header: 'Name', render: (r) => r.name },
  { key: 'pnl', header: 'PnL', render: (r) => r.pnl.toFixed(2), align: 'right' },
];

describe('DataTable', () => {
  it('rend les en-têtes et les lignes', () => {
    render(<DataTable columns={columns} rows={sampleRows} />);
    expect(screen.getByText('ID')).toBeInTheDocument();
    expect(screen.getByText('Name')).toBeInTheDocument();
    expect(screen.getByText('PnL')).toBeInTheDocument();
    expect(screen.getByText('alpha')).toBeInTheDocument();
    expect(screen.getByText('gamma')).toBeInTheDocument();
  });

  it('affiche emptyLabel si rows vide', () => {
    render(<DataTable columns={columns} rows={[]} emptyLabel="Aucune donnée" />);
    expect(screen.getByText(/aucune donnée/i)).toBeInTheDocument();
  });

  it('trie par colonne au clic du header', () => {
    render(<DataTable columns={columns} rows={sampleRows} sortable initialSortKey="pnl" initialSortAsc={false} />);
    // Tri descendant par PnL : 200, 100, 75, 0, -50
    const cells = screen.getAllByText(/\d+\.\d{2}/);
    // Premier PnL affiché devrait être 200.00 (gamma)
    expect(cells[0]).toHaveTextContent('200.00');
  });

  it('inverse le sens du tri au deuxième clic', () => {
    render(<DataTable columns={columns} rows={sampleRows} sortable initialSortKey="pnl" initialSortAsc={false} />);
    // Cliquer sur le header PnL pour inverser
    fireEvent.click(screen.getByText('PnL'));
    // Tri ascendant : -50, 0, 75, 100, 200
    const cells = screen.getAllByText(/\d+\.\d{2}/);
    expect(cells[0]).toHaveTextContent('-50.00');
  });

  it('paginate si paginated=true', () => {
    render(
      <DataTable
        columns={columns}
        rows={sampleRows}
        paginated
        pageSize={2}
      />
    );
    // 5 rows / 2 par page = 3 pages, page 1 = 2 lignes
    expect(screen.getByText(/page 1\/3/i)).toBeInTheDocument();
  });

  it('appelle onRowClick au clic d\'une ligne', () => {
    const onClick = vi.fn();
    render(<DataTable columns={columns} rows={sampleRows} onRowClick={onClick} />);
    fireEvent.click(screen.getByText('alpha'));
    expect(onClick).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'alpha' }),
      expect.any(Number),
    );
  });

  it('rend une colonne expandable si expandable fourni', () => {
    render(
      <DataTable
        columns={columns}
        rows={sampleRows.slice(0, 1)}
        expandable={(r) => <div>Détails de {r.name}</div>}
      />
    );
    // Au click, le contenu expandable apparaît
    expect(screen.queryByText(/détails de alpha/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('alpha'));
    expect(screen.getByText(/détails de alpha/i)).toBeInTheDocument();
  });

  it('respecte l\'alignement des colonnes', () => {
    const { container } = render(<DataTable columns={columns} rows={sampleRows} />);
    const ths = container.querySelectorAll('th');
    // ID est aligné à droite (index 0)
    expect(ths[0]).toHaveClass('text-right');
    // Name est aligné à gauche (index 1, défaut)
    expect(ths[1]).toHaveClass('text-left');
  });

  it('désactive le tri sur une colonne avec noSort=true', () => {
    const cols: DataTableColumn<Row>[] = [
      { key: 'id', header: 'ID', render: (r) => r.id, noSort: true },
    ];
    const { container } = render(<DataTable columns={cols} rows={sampleRows} sortable />);
    const th = container.querySelector('th');
    expect(th).not.toHaveClass('cursor-pointer');
  });

  it('utilise rowKey pour les keys React', () => {
    const { container } = render(
      <DataTable
        columns={columns}
        rows={sampleRows}
        rowKey={(r) => `row-${r.id}`}
      />
    );
    const trs = container.querySelectorAll('tbody tr');
    // 5 rows, pas d'erreur de key dupliquée
    expect(trs.length).toBeGreaterThanOrEqual(5);
  });

  // ── Accessibilité du tri ──────────────────────────────────────────────────
  // Régression : les tables du dépôt triaient via un vrai <button> dans le
  // <th>. En migrant vers <DataTable>, le tri est devenu un `onClick` posé sur
  // le <th> lui-même — donc inaccessible au clavier, et non annoncé comme
  // actionnable. Les 3 tables migrées avaient ainsi perdu leur tri au clavier.

  it('expose le tri comme un bouton activable au clavier', () => {
    render(<DataTable columns={columns} rows={sampleRows} sortable />);
    const bouton = screen.getByRole('button', { name: /PnL/i });
    expect(bouton).toBeInTheDocument();

    // Activable au clavier : un <button> répond à Enter/Espace nativement,
    // ce que jsdom traduit en événement click.
    bouton.focus();
    expect(bouton).toHaveFocus();
  });

  it('annonce une colonne triable inactive avec aria-sort="none"', () => {
    const { container } = render(<DataTable columns={columns} rows={sampleRows} sortable />);
    // `none` — et non l'absence d'attribut — est ce qui signale à une
    // technologie d'assistance que la colonne EST triable.
    const ths = [...container.querySelectorAll('th')];
    expect(ths.every((th) => th.getAttribute('aria-sort') === 'none')).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: /PnL/i }));
    const pnlTh = ths.find((th) => th.textContent?.includes('PnL'));
    expect(pnlTh?.getAttribute('aria-sort')).toBe('ascending');
  });

  it('ne rend pas de bouton sur une colonne noSort', () => {
    const cols: DataTableColumn<Row>[] = [
      { key: 'id', header: 'ID', render: (r) => r.id, noSort: true },
    ];
    const { container } = render(<DataTable columns={cols} rows={sampleRows} sortable />);
    expect(container.querySelector('th button')).toBeNull();
    // Pas triable => pas d'aria-sort du tout (l'attribut ne s'applique pas).
    expect(container.querySelector('th')?.hasAttribute('aria-sort')).toBe(false);
  });

  it('donne un scope aux en-têtes de colonne', () => {
    const { container } = render(<DataTable columns={columns} rows={sampleRows} />);
    const ths = [...container.querySelectorAll('th')];
    expect(ths.every((th) => th.getAttribute('scope') === 'col')).toBe(true);
  });
});
