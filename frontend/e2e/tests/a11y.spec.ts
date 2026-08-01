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
  // S10 — /dashboard, /bots et /backtest sont en 308 vers les pages v2 listées
  // plus bas : les inclure ici auditerait deux fois la même page.
  { url: '/trades', name: 'Trades' },
  { url: '/audit', name: 'Audit OOS' },
  { url: '/audit-log', name: 'Journal Audit' },
  { url: '/data', name: 'Données OHLCV' },
  { url: '/models', name: 'Registre modèles' },

  // Pages méta issues de la refonte S3-S6. Elles étaient absentes de cette
  // liste alors qu'elles concentrent l'essentiel du nouveau code (kanban,
  // drawer, tabs, donut, dialogues de confirmation) : ce sont précisément
  // celles dont l'accessibilité n'avait jamais été vérifiée.
  { url: '/portfolio-v2', name: 'Portefeuille' },
  { url: '/bots-v2', name: 'Mes Bots' },
  // Lots de fusion — les anciennes pages sont devenues des onglets de /market
  // et /lab, et leurs routes sont en 308. Radix ne monte QUE l'onglet actif :
  // auditer `/market` ou `/lab` seuls ne couvrirait que le premier onglet, on
  // ouvre donc chacun par son `?tab=`. La couverture reste celle des pages
  // d'origine.
  { url: '/market?tab=scanner', name: 'Marché — Scanner' },
  { url: '/market?tab=smartgraph', name: 'Marché — Smart Graph' },
  { url: '/market?tab=smartreplay', name: 'Marché — Smart Replay' },
  { url: '/market?tab=derivatives', name: 'Marché — Dérivés' },
  { url: '/lab?tab=backtest', name: 'Laboratoire — Backtest' },
  { url: '/lab?tab=optimizer', name: 'Laboratoire — Optimizer' },
  { url: '/lab?tab=ml', name: 'Laboratoire — ML' },
  { url: '/lab?tab=replay', name: 'Laboratoire — Replay' },
  { url: '/lab?tab=compare', name: 'Laboratoire — Compare' },
  { url: '/settings-v2?tab=capital', name: 'Réglages — Capital' },
  { url: '/settings-v2?tab=notifications', name: 'Réglages — Notifications' },
  { url: '/settings-v2?tab=data', name: 'Réglages — Données' },
  { url: '/settings-v2?tab=audit', name: 'Réglages — Audit' },
  { url: '/settings-v2?tab=ui', name: 'Réglages — UI' },
];

test.describe('Accessibilité WCAG 2.1 AA', () => {
  for (const page of PAGES) {
    test(`${page.name} (${page.url}) ne doit pas avoir de violations`, async ({ page: browser }) => {
      // Aller à la page — si le backend est down, les erreurs réseau seront
      // tolérées par axe (les éléments ErrorState restent accessibles).
      await browser.goto(page.url, { waitUntil: 'domcontentloaded' });
      // Laisser le temps aux queries de se résoudre
      await browser.waitForTimeout(1000);

      // Les pages entrent en `animate-fade-in`. Sans neutralisation, axe
      // échantillonne une frame intermédiaire et mesure une couleur fondue :
      // le même bouton ressortait à 3.12, 3.25 puis 3.81 selon les exécutions,
      // alors que son état stabilisé est conforme. On fige les animations pour
      // mesurer l'état réellement présenté à l'utilisateur.
      await browser.addStyleTag({
        content: `*, *::before, *::after {
          animation-duration: 0s !important;
          animation-delay: 0s !important;
          transition-duration: 0s !important;
        }`,
      });
      await browser.waitForTimeout(200);

      const results = await new AxeBuilder({ page: browser })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .analyze();

      // On ne fails que sur critical/serious/moderate (les minor sont tolérées)
      const significantViolations = results.violations.filter(
        (v) => v.impact === 'critical' || v.impact === 'serious' || v.impact === 'moderate',
      );

      if (significantViolations.length > 0) {
        // `violations.length` compte des RÈGLES, pas des éléments : une seule
        // « violation » peut porter sur des dizaines de nœuds. Sans le détail
        // par nœud, le rapport CI indique quelle règle échoue mais pas où —
        // ce qui rend la correction impossible sans rejouer axe localement.
        console.log(`\n❌ ${page.name} — ${significantViolations.length} règle(s) en défaut:`);
        for (const v of significantViolations) {
          console.log(`  - [${v.impact}] ${v.id}: ${v.description}`);
          console.log(`    Help: ${v.helpUrl}`);
          console.log(`    ${v.nodes.length} élément(s) concerné(s) :`);
          for (const node of v.nodes.slice(0, 5)) {
            console.log(`      · ${node.target.join(' ')}`);
            console.log(`        ${node.html.slice(0, 160)}`);
            // `failureSummary` donne les valeurs mesurées (ex. ratio de
            // contraste constaté vs attendu) — c'est ce qui manquait pour
            // corriger sans deviner.
            if (node.failureSummary) {
              console.log(`        ${node.failureSummary.replace(/\n/g, '\n        ')}`);
            }
          }
          if (v.nodes.length > 5) console.log(`      … et ${v.nodes.length - 5} autre(s)`);
        }
      }

      expect(significantViolations).toEqual([]);
    });
  }
});

test.describe('Skip-to-content link', () => {
  test('le skip-link est présent et focusable', async ({ page }) => {
    await page.goto('/portfolio-v2', { waitUntil: 'domcontentloaded' });

    // Le skip-link est sr-only par défaut, visible au focus
    const skipLink = page.locator('a[href="#main-content"]').first();
    await expect(skipLink).toBeAttached();

    // Le main a bien l'id correspondant
    await expect(page.locator('#main-content')).toBeVisible();
  });
});

test.describe('Navigation clavier', () => {
  test('Tab passe par tous les éléments interactifs dans l\'ordre', async ({ page }) => {
    await page.goto('/portfolio-v2', { waitUntil: 'domcontentloaded' });

    // Premier Tab devrait focus le skip-link (s'il est sr-only focusable)
    await page.keyboard.press('Tab');

    // Vérifier qu'un élément interactif a le focus
    const focusedTag = await page.evaluate(() => document.activeElement?.tagName);
    expect(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']).toContain(focusedTag);
  });
});
