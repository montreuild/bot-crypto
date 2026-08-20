'use client';

/**
 * QW-4 — Simulateur de coûts what-if.
 *
 * Permet de comparer l'impact des frais et du levier sur la stratégie en
 * relançant des backtests avec différents `cost_override` (cf. route
 * /api/backtest). Affiche 3 presets :
 *
 *   1. Spot / Levier 1 — référence (frais taker/maker standards, pas de borrow)
 *   2. Margin / Levier 3 — frais standards + borrow cost × 3
 *   3. Margin / Levier 10 — frais standards + borrow cost × 10 (risqué)
 *
 * Chaque preset lance un backtest en parallèle via `api.runBacktest` avec un
 * `cost_override` spécifique. Les résultats sont affichés dans une table
 * comparative (PnL net, frais totaux, borrow, Sharpe, Max DD, WR).
 *
 * Le panneau est replié par défaut (toggle) pour ne pas surcharger la page.
 * Quand l'utilisateur le déplie, les backtests sont lancés à la demande
 * (bouton « Lancer la comparaison ») — pas automatiquement au chargement.
 */

import { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { DataTable, type DataTableColumn } from '@/components/ui/data-table';
import { api } from '@/lib/api';
import type { CostModel, StrategyStats } from '@/types';
import { CostModelCard } from '@/components/cards/cost-model-card';
import { formatDrawdownPct } from '@/lib/backend-normalizers';
import { toast } from 'sonner';
import {
  Calculator, Loader2, AlertCircle,
} from 'lucide-react';
import {cn, formatUSD, errorMessage} from '@/lib/utils';

interface Props {
  symbol: string;
  timeframe: string;
  strategies: string;
  limit: number;
  /** Cost model actuel (pour afficher le contexte). */
  currentCostModel?: CostModel;
  className?: string;
}

// ── Presets de cost_override ────────────────────────────────────────────────
// Format CSV attendu par la route /api/backtest (cf. _apply_cost_override).
// Chaque preset définit les overrides à appliquer POUR CE RUN UNIQUEMENT.
interface Preset {
  key: string;
  label: string;
  description: string;
  costOverride: string;
  /** Couleur du badge pour distinguer les presets visuellement. */
  badge: 'muted' | 'warning' | 'danger';
}

const PRESETS: Preset[] = [
  {
    key: 'spot_lev1',
    label: 'Spot · Levier 1',
    description: 'Référence — frais taker/maker standards, pas de borrow',
    costOverride: 'max_leverage=1,borrow_rate_daily=0',
    badge: 'muted',
  },
  {
    key: 'margin_lev3',
    label: 'Margin · Levier 3',
    description: 'Frais standards + borrow cost × 3 (isolated margin)',
    costOverride: 'max_leverage=3,borrow_rate_daily=0.0005',
    badge: 'warning',
  },
  {
    key: 'margin_lev10',
    label: 'Margin · Levier 10',
    description: 'Frais standards + borrow cost × 10 (risqué — liquidation possible)',
    costOverride: 'max_leverage=10,borrow_rate_daily=0.001',
    badge: 'danger',
  },
];

// ── Type pour la table comparative ──────────────────────────────────────────
interface ComparisonRow {
  preset: string;
  presetKey: string;
  label: string;
  pnl: number | null;
  fees: number | null;
  borrow: number | null;
  sharpe: number | null;
  max_dd: number | null;
  win_rate: number | null;
  total_trades: number | null;
  error?: string | null;
  loading?: boolean;
}

export function CostSimulatorPanel({
  symbol, timeframe, strategies, limit, currentCostModel, className,
}: Props) {
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<Record<string, ComparisonRow>>({});
  const [ran, setRan] = useState(false);

  async function runComparison() {
    if (!symbol || !timeframe || !strategies) {
      toast.error('Symbole, timeframe et stratégie requis');
      return;
    }
    setLoading(true);
    // Initialiser les lignes en loading
    const initial: Record<string, ComparisonRow> = {};
    for (const p of PRESETS) {
      initial[p.key] = {
        preset: p.label,
        presetKey: p.key,
        label: p.label,
        pnl: null, fees: null, borrow: null, sharpe: null,
        max_dd: null, win_rate: null, total_trades: null,
        loading: true,
      };
    }
    setResults(initial);

    // Séquentiel : 3 backtests parallèles saturent le pool et laissent les
    // lignes margin vides (timeout / erreur silencieuse).
    const newResults: Record<string, ComparisonRow> = { ...initial };
    for (const preset of PRESETS) {
      let row: ComparisonRow;
      try {
        const res = await api.runBacktest({
          symbol,
          timeframe,
          strategies,
          limit,
          cost_override: preset.costOverride,
        });
        const payload = Array.isArray(res) ? res[0] : res;
        const byStrategy = payload?.by_strategy ?? {};
        const firstStrat = (Object.values(byStrategy)[0] ?? {}) as StrategyStats;
        const empty = !firstStrat || Object.keys(firstStrat).length === 0;
        row = {
          preset: preset.label,
          presetKey: preset.key,
          label: preset.label,
          pnl: firstStrat.total_pnl ?? null,
          fees: firstStrat.total_fees ?? null,
          borrow: firstStrat.total_borrow_cost ?? null,
          sharpe: firstStrat.sharpe ?? null,
          max_dd: firstStrat.max_drawdown ?? null,
          win_rate: firstStrat.win_rate ?? null,
          total_trades: firstStrat.total_trades ?? null,
          error: empty ? 'Pas de résultat' : null,
          loading: false,
        };
      } catch (e) {
        row = {
          preset: preset.label,
          presetKey: preset.key,
          label: preset.label,
          pnl: null, fees: null, borrow: null, sharpe: null,
          max_dd: null, win_rate: null, total_trades: null,
          error: errorMessage(e) || 'Erreur',
          loading: false,
        };
      }
      newResults[preset.key] = row;
      setResults({ ...newResults });
    }
    setLoading(false);
    setRan(true);
  }

  useEffect(() => {
    if (ran || loading) return;
    if (!symbol || !timeframe || !strategies) return;
    void runComparison();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, timeframe, strategies, limit]);

  // Colonnes pour la table comparative
  const columns: DataTableColumn<ComparisonRow>[] = [
    {
      key: 'label',
      header: 'Preset',
      align: 'left',
      noSort: true,
      render: (r) => {
        const preset = PRESETS.find((p) => p.key === r.presetKey);
        return (
          <span className="flex items-center gap-2">
            <Badge variant={preset?.badge ?? 'muted'} className="text-[10px]">
              {r.label}
            </Badge>
            {r.loading && <Loader2 className="w-3 h-3 animate-spin text-muted" />}
            {r.error && (
              <span className="inline-flex items-center gap-1 text-[10px] text-red-400 max-w-[14rem]" title={r.error}>
                <AlertCircle className="w-3 h-3 shrink-0" />
                <span className="truncate">{r.error}</span>
              </span>
            )}
          </span>
        );
      },
    },
    {
      key: 'total_trades',
      header: 'Trades',
      align: 'right',
      render: (r) => <span className="font-mono">{r.total_trades ?? '—'}</span>,
    },
    {
      key: 'pnl',
      header: 'PnL net',
      align: 'right',
      sortValue: (r) => r.pnl ?? -Infinity,
      render: (r) => (
        <span className={cn(
          'font-mono font-semibold',
          r.pnl == null ? 'text-muted' : r.pnl >= 0 ? 'text-emerald-400' : 'text-red-400',
        )}>
          {r.pnl == null ? '—' : formatUSD(r.pnl, { sign: true })}
        </span>
      ),
    },
    {
      key: 'fees',
      header: 'Frais',
      align: 'right',
      render: (r) => <span className="font-mono text-amber-400">{r.fees == null ? '—' : formatUSD(r.fees)}</span>,
    },
    {
      key: 'borrow',
      header: 'Borrow',
      align: 'right',
      render: (r) => <span className="font-mono text-orange-400">{r.borrow == null ? '—' : formatUSD(r.borrow)}</span>,
    },
    {
      key: 'sharpe',
      header: 'Sharpe',
      align: 'right',
      sortValue: (r) => r.sharpe ?? -Infinity,
      render: (r) => <span className="font-mono">{r.sharpe == null ? '—' : r.sharpe.toFixed(2)}</span>,
    },
    {
      key: 'max_dd',
      header: 'Max DD',
      align: 'right',
      render: (r) => (
        <span className="font-mono text-red-400">
          {r.max_dd == null ? '—' : formatDrawdownPct(r.max_dd)}
        </span>
      ),
    },
    {
      key: 'win_rate',
      header: 'WR',
      align: 'right',
      render: (r) => <span className="font-mono">{r.win_rate == null ? '—' : `${r.win_rate.toFixed(1)}%`}</span>,
    },
  ];

  const rows = PRESETS.map((p) => results[p.key] ?? {
    preset: p.label, presetKey: p.key, label: p.label,
    pnl: null, fees: null, borrow: null, sharpe: null,
    max_dd: null, win_rate: null, total_trades: null,
  });

  return (
    <div className={cn('space-y-3', className)}>
      <CostModelCard model={currentCostModel} />
      <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Calculator className="w-4 h-4" />
          Simulateur de coûts
          {loading && <Loader2 className="w-3.5 h-3.5 animate-spin text-muted" />}
        </CardTitle>
        <p className="text-[11px] text-muted-foreground font-normal">
          Spot / margin×3 / margin×10 — même fenêtre, calculé automatiquement.
        </p>
      </CardHeader>
      <CardContent>
          <div className="mb-3 grid grid-cols-1 md:grid-cols-3 gap-2 text-[11px]">
            {PRESETS.map((p) => (
              <div key={p.key} className="flex items-start gap-2 p-2 rounded-md bg-card-hover/30">
                <Badge variant={p.badge} className="text-[10px] flex-shrink-0">
                  {p.label}
                </Badge>
                <span className="text-muted-foreground">{p.description}</span>
              </div>
            ))}
          </div>

          <DataTable
            columns={columns}
            rows={rows}
            sortable
            initialSortKey="pnl"
            initialSortAsc={false}
            rowKey={(r) => r.presetKey}
            emptyLabel="Calcul des presets en cours…"
          />

          <p className="text-[10px] text-dim mt-3 italic">
            ⚠ Les backtests what-if utilisent les mêmes données et stratégies
            que le backtest principal — seul le cost_model est surchargé pour
            ce run. Le levier affecte le sizing (capital × levier) et le borrow
            cost (margin hourly rate × durée de position).
          </p>
        </CardContent>
    </Card>
    </div>
  );
}
