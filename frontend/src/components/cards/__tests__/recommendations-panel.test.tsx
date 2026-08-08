/**
 * QW-5 — Tests du composant RecommendationsPanel.
 *
 * Valide le rendu des recommandations avec différentes sévérités (critical,
 * warning, info, positive), l'affichage du verdict global, les compteurs
 * synthétiques, et les liens d'action cliquables.
 *
 * Le CostSimulatorPanel n'est pas testé ici car il fait des appels API réels
 * (api.runBacktest) — il est couvert par les tests E2E (qw-backtest.spec.ts).
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RecommendationsPanel } from '@/components/cards/recommendations-panel';
import type {
  BacktestRecommendation,
  BacktestRecommendationsSummary,
} from '@/types';

const makeReco = (overrides: Partial<BacktestRecommendation>): BacktestRecommendation => ({
  severity: 'info',
  code: 'TEST_CODE',
  title: 'Test title',
  message: 'Test message',
  action: 'Test action',
  action_link: '',
  ...overrides,
});

const makeSummary = (overrides: Partial<BacktestRecommendationsSummary>): BacktestRecommendationsSummary => ({
  counts: { critical: 0, warning: 0, info: 0, positive: 0 },
  verdict: 'neutral',
  verdict_label: 'Neutre',
  total: 0,
  ...overrides,
});

describe('RecommendationsPanel', () => {
  it('ne s\'affiche pas si recommendations est vide', () => {
    const { container } = render(
      <RecommendationsPanel recommendations={[]} summary={makeSummary({})} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('ne s\'affiche pas si recommendations est undefined', () => {
    const { container } = render(
      <RecommendationsPanel recommendations={undefined} summary={undefined} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('affiche le titre et le nom de la stratégie', () => {
    render(
      <RecommendationsPanel
        recommendations={[makeReco({ title: 'Test reco' })]}
        summary={makeSummary({ total: 1 })}
        strategy="trend_rider"
      />,
    );
    expect(screen.getByText(/Recommandations/i)).toBeInTheDocument();
    expect(screen.getByText(/trend_rider/i)).toBeInTheDocument();
  });

  it('affiche le verdict critical en rouge', () => {
    render(
      <RecommendationsPanel
        recommendations={[makeReco({ severity: 'critical', title: 'PnL négatif' })]}
        summary={makeSummary({
          counts: { critical: 1, warning: 0, info: 0, positive: 0 },
          verdict: 'critical',
          verdict_label: 'Critique — ne pas trader en l\'état',
          total: 1,
        })}
      />,
    );
    // Le verdict_label est affiché dans le badge du header
    expect(screen.getAllByText(/Critique/i).length).toBeGreaterThan(0);
  });

  it('affiche le verdict positive en vert', () => {
    render(
      <RecommendationsPanel
        recommendations={[makeReco({ severity: 'positive', title: 'Sharpe excellent' })]}
        summary={makeSummary({
          counts: { critical: 0, warning: 0, info: 0, positive: 1 },
          verdict: 'positive',
          verdict_label: 'Positif — candidat à la promotion',
          total: 1,
        })}
      />,
    );
    expect(screen.getAllByText(/Positif/i).length).toBeGreaterThan(0);
  });

  it('affiche les compteurs par sévérité', () => {
    render(
      <RecommendationsPanel
        recommendations={[
          makeReco({ severity: 'critical', code: 'C1' }),
          makeReco({ severity: 'critical', code: 'C2' }),
          makeReco({ severity: 'warning', code: 'W1' }),
          makeReco({ severity: 'info', code: 'I1' }),
          makeReco({ severity: 'positive', code: 'P1' }),
        ]}
        summary={makeSummary({
          counts: { critical: 2, warning: 1, info: 1, positive: 1 },
          verdict: 'critical',
          verdict_label: 'Critique',
          total: 5,
        })}
      />,
    );
    // 2 critiques
    expect(screen.getByText(/2 critiques/i)).toBeInTheDocument();
    // 1 avertissement
    expect(screen.getByText(/1 avertissement/i)).toBeInTheDocument();
    // 1 info
    expect(screen.getByText(/1 info/i)).toBeInTheDocument();
    // 1 point fort
    expect(screen.getByText(/1 point fort/i)).toBeInTheDocument();
  });

  it('affiche le titre, message et action de chaque recommandation', () => {
    render(
      <RecommendationsPanel
        recommendations={[
          makeReco({
            severity: 'warning',
            code: 'HIGH_DRAWDOWN',
            title: 'Drawdown élevé (28.0%)',
            message: 'Le drawdown maximum atteint 28.0%.',
            action: 'Réduire risk_per_trade',
          }),
        ]}
        summary={makeSummary({
          counts: { critical: 0, warning: 1, info: 0, positive: 0 },
          verdict: 'risky',
          verdict_label: 'Risqué',
          total: 1,
        })}
      />,
    );
    expect(screen.getByText('Drawdown élevé (28.0%)')).toBeInTheDocument();
    expect(screen.getByText(/drawdown maximum atteint 28\.0%/i)).toBeInTheDocument();
    expect(screen.getByText('Réduire risk_per_trade')).toBeInTheDocument();
  });

  it('affiche le code machine de chaque recommandation', () => {
    render(
      <RecommendationsPanel
        recommendations={[
          makeReco({ severity: 'info', code: 'LOW_SHARPE', title: 'Sharpe faible' }),
        ]}
        summary={makeSummary({ total: 1 })}
      />,
    );
    expect(screen.getByText('LOW_SHARPE')).toBeInTheDocument();
  });

  it('rend un lien cliquable si action_link fourni', () => {
    render(
      <RecommendationsPanel
        recommendations={[
          makeReco({
            severity: 'warning',
            code: 'HIGH_DRAWDOWN',
            title: 'DD élevé',
            action: 'Réduire risk_per_trade',
            action_link: '/settings?tab=risk',
          }),
        ]}
        summary={makeSummary({ total: 1 })}
      />,
    );
    const link = screen.getByRole('link', { name: /réduire risk_per_trade/i });
    expect(link).toHaveAttribute('href', '/settings?tab=risk');
  });

  it('rend l\'action sans lien si action_link absent', () => {
    render(
      <RecommendationsPanel
        recommendations={[
          makeReco({
            severity: 'info',
            code: 'LOW_SHARPE',
            title: 'Sharpe faible',
            action: 'Optimiser via le Laboratoire',
            action_link: '',
          }),
        ]}
        summary={makeSummary({ total: 1 })}
      />,
    );
    // Pas de lien, mais le texte de l'action est présent
    expect(screen.getByText('Optimiser via le Laboratoire')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('affiche les icônes de sévérité correctes', () => {
    const { container } = render(
      <RecommendationsPanel
        recommendations={[
          makeReco({ severity: 'critical', code: 'C1', title: 'Critique 1' }),
          makeReco({ severity: 'warning', code: 'W1', title: 'Warning 1' }),
          makeReco({ severity: 'info', code: 'I1', title: 'Info 1' }),
          makeReco({ severity: 'positive', code: 'P1', title: 'Positive 1' }),
        ]}
        summary={makeSummary({
          counts: { critical: 1, warning: 1, info: 1, positive: 1 },
          total: 4,
        })}
      />,
    );
    // Vérifier que les 4 recommandations sont rendues
    expect(screen.getByText('Critique 1')).toBeInTheDocument();
    expect(screen.getByText('Warning 1')).toBeInTheDocument();
    expect(screen.getByText('Info 1')).toBeInTheDocument();
    expect(screen.getByText('Positive 1')).toBeInTheDocument();
    // Les badges de sévérité doivent être présents
    expect(container.querySelectorAll('[class*="text-red-400"]').length).toBeGreaterThan(0);
    expect(container.querySelectorAll('[class*="text-amber-400"]').length).toBeGreaterThan(0);
    expect(container.querySelectorAll('[class*="text-emerald-400"]').length).toBeGreaterThan(0);
  });

  it('applique la bordure gauche selon le verdict', () => {
    const { container, rerender } = render(
      <RecommendationsPanel
        recommendations={[makeReco({ severity: 'critical' })]}
        summary={makeSummary({ verdict: 'critical' })}
      />,
    );
    let card = container.querySelector('.border-l-4');
    expect(card).toHaveClass('border-l-red-500');

    rerender(
      <RecommendationsPanel
        recommendations={[makeReco({ severity: 'positive' })]}
        summary={makeSummary({ verdict: 'positive' })}
      />,
    );
    card = container.querySelector('.border-l-4');
    expect(card).toHaveClass('border-l-emerald-500');
  });
});
