/**
 * Les axes d'analyse produits par L0–L6 doivent être LISIBLES, pas seulement
 * présents dans le payload : un champ journalisé que l'interface n'affiche pas
 * est une mesure que personne ne lira.
 *
 * Les trades ci-dessous ont la forme que `Backtester._close_at` produit
 * réellement — état de structure, séquence, tier, classe de cible, jambes
 * partielles — et non une forme inventée pour le test.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TradesStatsPanel } from '@/components/cards/trades-stats-panel';
import type { BacktestTrade } from '@/types';

const BASE: BacktestTrade = {
  time: '2021-01-01T00:00:00', symbol: 'BTC/USDC', side: 'long',
  strategy: 'smart_money', entry: 100, exit: 102, pnl: 20, pnl_pct: 2,
  fees: 0.5, reason: 'LONG SWEEP_REVERSAL', status: 'closed',
  exit_reason: 'take_profit',
};

const SMC: BacktestTrade[] = [
  {
    ...BASE, id: 1, setup: 'SWEEP_REVERSAL', module: 'SMC_CORE',
    structure_state: 'BULLISH_PULLBACK', sequence_type: 'CONTINUATION',
    tier: 'A', indicators: { tp_class: 'PREV_WEEK' } as never,
  },
  {
    ...BASE, id: 2, pnl: -12, exit_reason: 'stop_loss',
    setup: 'OB_RETEST', module: 'SMC_CORE',
    structure_state: 'BEARISH_WARNING', sequence_type: 'EARLY_REVERSAL',
    tier: 'C', indicators: { tp_class: 'SWING' } as never,
    exits: [{ bar: 10, price: 101, size: 0.25, fraction: 0.25, reason: 'tp1', pnl: 3 }],
  },
];

/** Le cas de toutes les stratégies non-SMC : aucun champ nouveau. */
const NU: BacktestTrade[] = [{ ...BASE, id: 3 }];

describe('TradesStatsPanel — axes SMC', () => {
  it('affiche un tableau par axe renseigné', () => {
    render(<TradesStatsPanel trades={SMC} />);
    expect(screen.getByText('Par setup')).toBeInTheDocument();
    expect(screen.getByText(/Par module SMC\/ICT/)).toBeInTheDocument();
    expect(screen.getByText(/Par état de structure/)).toBeInTheDocument();
    expect(screen.getByText(/Par type de séquence/)).toBeInTheDocument();
    expect(screen.getByText(/Par tier/)).toBeInTheDocument();
    expect(screen.getByText(/Par classe de cible/)).toBeInTheDocument();
  });

  it('montre les valeurs et non seulement les en-têtes', () => {
    render(<TradesStatsPanel trades={SMC} />);
    expect(screen.getByText('BULLISH_PULLBACK')).toBeInTheDocument();
    expect(screen.getByText('EARLY_REVERSAL')).toBeInTheDocument();
    expect(screen.getByText('PREV_WEEK')).toBeInTheDocument();
  });

  it('compte les jambes partielles quand il y en a', () => {
    render(<TradesStatsPanel trades={SMC} />);
    expect(screen.getByText('Sorties partielles')).toBeInTheDocument();
    expect(screen.getByText('1 / 1 tr.')).toBeInTheDocument();
  });

  it("n'affiche aucun axe absent — le cas de toutes les stratégies non-SMC", () => {
    render(<TradesStatsPanel trades={NU} />);
    expect(screen.queryByText(/Par état de structure/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Par tier/)).not.toBeInTheDocument();
    expect(screen.queryByText('Sorties partielles')).not.toBeInTheDocument();
    // Les statistiques historiques, elles, restent affichées.
    expect(screen.getByText('Par raison de sortie')).toBeInTheDocument();
  });

  it('conserve les chips de base', () => {
    render(<TradesStatsPanel trades={SMC} />);
    expect(screen.getByText('WR Long')).toBeInTheDocument();
    expect(screen.getByText('Avg Win')).toBeInTheDocument();
  });
});

describe('Répartition du PnL par jambe de sortie (§5)', () => {
  /* Un trade fractionné : 25 % à TP1 (+8), 25 % à TP2 (+12), et un reliquat
     qui porte le solde. Le total du trade vaut 50, donc le runner vaut 30.
     Les nombres sont choisis pour que la reconstitution par différence soit
     vérifiable de tête. */
  const FRACTIONNE: BacktestTrade[] = [
    {
      ...BASE, id: 10, pnl: 50, exit_reason: 'trailing_stop',
      exits: [
        { bar: 5, price: 108, size: 0.25, fraction: 0.25, reason: 'tp1', pnl: 8 },
        { bar: 9, price: 116, size: 0.25, fraction: 0.25, reason: 'tp2', pnl: 12 },
      ],
    } as BacktestTrade,
  ];

  it('affiche une ligne par jambe, reliquat compris', () => {
    render(<TradesStatsPanel trades={FRACTIONNE} />);
    expect(screen.getByText(/Par poste de sortie/i)).toBeInTheDocument();
    expect(screen.getByText('tp1')).toBeInTheDocument();
    expect(screen.getByText('tp2')).toBeInTheDocument();
    expect(screen.getByText('runner')).toBeInTheDocument();
  });

  it('reconstitue le reliquat par différence', () => {
    /* 50 − (8 + 12) = 30. Sans cette ligne, le PnL du reliquat serait
       invisible : il part avec la clôture du trade et n'est jamais journalisé
       comme une jambe. */
    render(<TradesStatsPanel trades={FRACTIONNE} />);
    expect(screen.getByText('+$30.00')).toBeInTheDocument();
    expect(screen.getByText('+$8.00')).toBeInTheDocument();
    expect(screen.getByText('+$12.00')).toBeInTheDocument();
  });

  it('classe les trades non fractionnés dans un poste « complet »', () => {
    /* Une stratégie tout-ou-rien doit apparaître, pas disparaître : son PnL
       compte autant que celui des trades fractionnés.

       La série `SMC` ne convient PAS ici : elle porte déjà des jambes
       partielles (cf. en-tête du fichier). Il faut une série explicitement
       sans `exits`, sinon le test passerait pour la mauvaise raison. */
    const TOUT_OU_RIEN: BacktestTrade[] = [
      { ...BASE, id: 20, setup: 'OB_RETEST', module: 'SMC_CORE' },
    ];
    render(<TradesStatsPanel trades={TOUT_OU_RIEN} />);
    // Le tableau existe toujours — mais avec un seul poste « complet », et non
    // une ligne de jambe. Le faire disparaître masquerait le PnL de ces trades.
    expect(screen.getByText(/complet ·/)).toBeInTheDocument();
  });

  it('couvre AUSSI les trades sortis en une fois', () => {
    /* L'invariant qui manquait : la somme des postes doit redonner le PnL
       total. Sans le poste « complet », le tableau n'affichait que les trades
       fractionnés — tous gagnants par construction — et annonçait 695 % du PnL
       réel avec 100 % de réussite sur chaque ligne. */
    const MIXTE: BacktestTrade[] = [
      ...FRACTIONNE,
      { ...BASE, id: 30, pnl: -40, exit_reason: 'stop_loss' },
      { ...BASE, id: 31, side: 'short', pnl: -10, exit_reason: 'stop_loss' },
    ];
    render(<TradesStatsPanel trades={MIXTE} />);
    // 8 + 12 + 30 (reliquat) − 40 − 10 = 0 : les pertes DOIVENT apparaître.
    // On ancre sur LA LIGNE : le montant existe aussi dans « Par raison de
    // sortie », donc une recherche par texte global trouverait deux éléments
    // et ne prouverait pas que c'est le bon tableau qui l'affiche.
    const cellule = screen.getByText('complet · stop_loss');
    const ligne = cellule.closest('tr');
    expect(ligne).not.toBeNull();
    expect(ligne).toHaveTextContent('$-50.00');
    expect(ligne).toHaveTextContent('2');           // les deux trades stoppés
  });
});
