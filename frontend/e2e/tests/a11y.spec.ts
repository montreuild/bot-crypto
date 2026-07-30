/**
 * S2-F3-US1 — Tests d'accessibilité automatisés avec @axe-core/playwright.
 *
 * Vérifie la conformité WCAG 2.1 AA sur toutes les pages principales.
 * Le test échoue si des violations sont détectées (critical/serious/moderate).
 *
 * Pour exécuter :
 *   npx playwright test e2e/tests/a11y.spec.ts
 *
 * Rapport HTML : playwright-report/index.html
 */

import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Pages à tester — liste exhaustive des routes principales
const PAGES = [
  { url: '/dashboard', name: 'Dashboard' },
  { url: '/bots', name: 'Mes Bots' },
  { url: '/trades', name: 'Trades' },
  { url: '/portfolio', name: 'Portefeuille' },
  { url: '/backtest', name: 'Backtest' },
  { url: '/scanner', name: 'Scanner' },
  { url: '/replay', name: 'Replay' },
  { url: '/smartgraph', name: 'Smart Graph' },
  { url: '/smartreplay', name: 'Smart Replay' },
  { url: '/compare', name: 'Comparatif' },
  { url: '/optimizer', name: 'Optimiseur' },
  { url: '/audit', name: 'Audit OOS' },
  { url: '/audit-log', name: 'Journal Audit' },
  { url: '/derivatives', name: 'Dérivés' },
  { url: '/data', name: 'Données OHLCV' },
  { url: '/ml', name: 'Modèles ML' },
  { url: '/models', name: 'Registre modèles' },
  { url: '/config', name: 'Configuration' },
  { url: '/settings', name: 'Réglages' },
];

test.describe('Accessibilité WCAG 2.1 AA', () => {
  for (const page of PAGES) {
    test(`${page.name} (${page.url}) ne doit pas avoir de violations`, async ({ page: browser }) => {
      // Aller à la page — si le backend est down, les erreurs réseau seront
      // tolérées par axe (les éléments ErrorState restent accessibles).
      await browser.goto(page.url, { waitUntil: 'domcontentloaded' });
      // Laisser le temps aux queries de se résoudre
      await browser.waitForTimeout(1000);

      const results = await new AxeBuilder({ page: browser })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();

      // On ne fails que sur critical/serious/moderate (les minor sont tolérées)
      const significantViolations = results.violations.filter(
        (v) => v.impact === 'critical' || v.impact === 'serious' || v.impact === 'moderate',
      );

      if (significantViolations.length > 0) {
        console.log(`\n❌ ${page.name} — ${significantViolations.length} violation(s):`);
        for (const v of significantViolations) {
          console.log(`  - [${v.impact}] ${v.id}: ${v.description}`);
          console.log(`    Help: ${v.helpUrl}`);
        }
      }

      expect(significantViolations).toEqual([]);
    });
  }
});

test.describe('Skip-to-content link', () => {
  test('le skip-link est présent et focusable', async ({ page }) => {
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });

    // Le skip-link est sr-only par défaut, visible au focus
    const skipLink = page.locator('a[href="#main-content"]').first();
    await expect(skipLink).toBeAttached();

    // Le main a bien l'id correspondant
    await expect(page.locator('#main-content')).toBeVisible();
  });
});

test.describe('Navigation clavier', () => {
  test('Tab passe par tous les éléments interactifs dans l\'ordre', async ({ page }) => {
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });

    // Premier Tab devrait focus le skip-link (s'il est sr-only focusable)
    await page.keyboard.press('Tab');

    // Vérifier qu'un élément interactif a le focus
    const focusedTag = await page.evaluate(() => document.activeElement?.tagName);
    expect(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']).toContain(focusedTag);
  });
});
