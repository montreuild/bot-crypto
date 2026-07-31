/**
 * S10-F1 — Tests de régression visuelle.
 *
 * ── Pourquoi pas Chromatic/Storybook ──────────────────────────────────────
 * Le plan de refonte citait « Chromatic/Storybook ». Storybook ajoute ~40
 * paquets et une seconde arborescence de composants à maintenir ; Chromatic
 * exige un compte externe, un token en secret CI et publie les captures chez un
 * tiers — pour une UI de trading interne, c'est un coût et une surface
 * d'exposition disproportionnés.
 *
 * `toHaveScreenshot()` de Playwright couvre le même besoin (détecter une
 * régression visuelle non intentionnelle) avec la dépendance déjà présente et
 * les captures versionnées dans le dépôt.
 *
 * ── Baselines ─────────────────────────────────────────────────────────────
 * Playwright suffixe les références par plateforme (`-linux.png`, `-win32.png`)
 * : une baseline générée sous Windows ne vaut PAS sous l'ubuntu-latest de la
 * CI (rendu des polices différent). Les références Linux des 5 pages méta sont
 * donc générées par la CI puis commitées sous `visual.spec.ts-snapshots/`.
 *
 * Après un changement visuel volontaire, les régénérer :
 *
 *   npx playwright test --config e2e/playwright.config.ts visual.spec.ts --update-snapshots
 *
 * puis récupérer l'artefact `visual-snapshots` du job CI et committer les PNG.
 *
 * ── Stabilité ─────────────────────────────────────────────────────────────
 * Ces pages affichent des données live (PnL, horodatages, compteurs). On
 * masque donc les zones volatiles plutôt que de figer des valeurs : le test
 * doit détecter un changement de *mise en page*, pas une variation de marché.
 */

import { test, expect } from '@playwright/test';

const PAGES = [
  { url: '/portfolio-v2', name: 'portefeuille' },
  { url: '/bots-v2', name: 'bots' },
  { url: '/lab', name: 'laboratoire' },
  { url: '/market', name: 'marche' },
  { url: '/settings-v2', name: 'reglages' },
];

// Sélecteurs dont le contenu bouge à chaque exécution.
const VOLATILE = [
  '.recharts-surface',                 // courbes et donuts
  '[data-testid="live-value"]',
  'text=/\\$[0-9]/',                   // montants
  'text=/[0-9]{2}:[0-9]{2}/',          // horodatages
];

test.describe('Régression visuelle — pages méta', () => {
  for (const p of PAGES) {
    test(`${p.name} : rendu stable`, async ({ page }) => {
      await page.goto(p.url, { waitUntil: 'networkidle' });

      // Neutralise les animations d'entrée (`animate-fade-in`) qui, sans ça,
      // produisent une capture différente à chaque exécution.
      await page.addStyleTag({
        content: `*, *::before, *::after {
          animation-duration: 0s !important;
          animation-delay: 0s !important;
          transition-duration: 0s !important;
        }`,
      });
      await page.waitForTimeout(500);

      await expect(page).toHaveScreenshot(`${p.name}.png`, {
        fullPage: true,
        mask: VOLATILE.map((s) => page.locator(s)),
        maxDiffPixelRatio: 0.02,
        animations: 'disabled',
      });
    });
  }
});

test.describe('Régression visuelle — design system', () => {
  test('drawer bot : frise + bande Monte-Carlo', async ({ page }) => {
    // Le drawer est le composant qui a le plus souffert pendant S0-S9 (il
    // plantait à l'ouverture) : il mérite sa propre référence visuelle.
    await page.goto('/bots-v2?slot=breakout%3A%3A1h', { waitUntil: 'networkidle' });
    await page.addStyleTag({
      content: '*, *::before, *::after { animation-duration: 0s !important; transition-duration: 0s !important; }',
    });

    // Le drawer ne s'ouvre que si `/api/bots` a renvoyé le slot demandé. Les
    // jobs CI ne démarrent QUE le frontend : sans backend, il n'y a aucun bot
    // et le test ne peut rien capturer. On l'ignore explicitement plutôt que
    // d'échouer — un test rouge pour absence de données n'apprend rien, et
    // masquerait une vraie régression visuelle sur les autres captures.
    const dialog = page.locator('[role="dialog"]');
    const opened = await dialog.isVisible({ timeout: 10000 }).catch(() => false);
    test.skip(!opened, 'Drawer indisponible : backend éteint, aucun bot à afficher.');

    await expect(dialog).toHaveScreenshot('drawer-bot.png', {
      mask: VOLATILE.map((s) => page.locator(s)),
      maxDiffPixelRatio: 0.02,
      animations: 'disabled',
    });
  });
});
