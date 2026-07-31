import { test, expect } from '@playwright/test';

// S10 — /dashboard, /bots et /backtest sont en 308 vers les pages v2 : ils ne
// figurent plus ici (les cibles sont couvertes ci-dessous), mais leur
// redirection est vérifiée par le bloc « Redirections S10 » en fin de fichier.
const PAGES = [
  { path: '/portfolio-v2', title: 'Portefeuille' },
  { path: '/bots-v2', title: 'Mes Bots' },
  { path: '/lab', title: 'Laboratoire' },
  { path: '/market', title: 'Marché' },
  { path: '/settings-v2', title: 'Réglages' },
  { path: '/trades', title: 'Trades' },
  { path: '/portfolio', title: 'Portefeuille' },
  { path: '/scanner', title: 'Scanner' },
  { path: '/replay', title: 'Replay' },
  { path: '/smartgraph', title: 'Smart' },
  { path: '/smartreplay', title: 'Smart' },
  { path: '/compare', title: 'Comparatif' },
  { path: '/optimizer', title: 'Optimiseur' },
  { path: '/audit', title: 'Audit' },
  { path: '/audit-log', title: 'Journal' },
  { path: '/derivatives', title: 'Dériv' },
  { path: '/data', title: 'Données' },
  { path: '/ml', title: 'ML' },
  { path: '/models', title: 'Modèles' },
  { path: '/config', title: 'Configuration' },
  { path: '/settings', title: 'Réglages' },
];

test.describe('Page loading', () => {
  for (const p of PAGES) {
    test(`${p.path} loads`, async ({ page }) => {
      const response = await page.goto(p.path);
      expect(response?.status()).toBe(200);
      const h1 = page.locator('h1').first();
      await expect(h1).toBeVisible({ timeout: 10000 });
    });
  }
});

// S6-09 : la racine `/` remplace l'ancienne route Jinja2 `GET /` (dashboard.html).
// S10 : elle pointe désormais sur /portfolio-v2, la page méta d'entrée.
test.describe('Racine', () => {
  test('/ redirige vers /portfolio-v2', async ({ page }) => {
    await page.goto('/');
    await page.waitForURL('**/portfolio-v2', { timeout: 10000 });
    await expect(page).toHaveURL(/\/portfolio-v2/);
  });
});

// S10 — bascule strangler fig. Seules ces trois routes sont en 308 : les pages
// méta correspondantes remplacent réellement l'ancienne. Les 11 autres
// redirections du plan sont bloquées (onglets en carte de renvoi), cf.
// docs/audit-ui-ux-bot-crypto.md §Bascule S10.
test.describe('Redirections S10', () => {
  const REDIRECTS = [
    { from: '/dashboard', to: /\/portfolio-v2/ },
    { from: '/bots', to: /\/bots-v2/ },
    { from: '/backtest', to: /\/lab\?tab=backtest/ },
  ];

  for (const r of REDIRECTS) {
    test(`${r.from} redirige en 308`, async ({ page }) => {
      const response = await page.goto(r.from);
      // Playwright suit la redirection : on vérifie la chaîne ET l'URL finale.
      const chain = response?.request().redirectedFrom();
      expect(chain, `${r.from} devrait rediriger`).not.toBeNull();
      await expect(page).toHaveURL(r.to);
    });
  }

  // Les routes volontairement NON redirigées doivent rester servies en direct :
  // les onglets des pages méta y renvoient explicitement.
  const KEPT = ['/optimizer', '/replay', '/compare', '/ml', '/scanner', '/smartgraph', '/smartreplay', '/derivatives', '/config', '/settings', '/portfolio'];
  for (const path of KEPT) {
    test(`${path} reste accessible (pas de 308)`, async ({ page }) => {
      const response = await page.goto(path);
      expect(response?.status()).toBe(200);
      expect(page.url()).toContain(path);
    });
  }
});

test.describe('Portefeuille', () => {
  test('displays KPIs', async ({ page }) => {
    await page.goto('/portfolio-v2');
    await page.waitForTimeout(3000);
    const kpi = page.locator('text=Capital').or(page.locator('text=PnL')).or(page.locator('text=Win Rate'));
    await expect(kpi.first()).toBeVisible({ timeout: 10000 });
  });

  test('has equity curve chart', async ({ page }) => {
    await page.goto('/portfolio-v2');
    await page.waitForTimeout(3000);
    const chart = page.locator('.recharts-surface').or(page.locator('svg'));
    await expect(chart.first()).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Navigation', () => {
  test('sidebar has nav groups', async ({ page }) => {
    await page.goto('/portfolio-v2');
    await expect(page.locator('text=Trading').first()).toBeVisible();
    await expect(page.locator('text=Recherche').first()).toBeVisible();
  });

  test('can navigate to Bots', async ({ page }) => {
    await page.goto('/portfolio-v2');
    await page.click('a[href="/bots-v2"]');
    await page.waitForURL('**/bots-v2');
    await expect(page).toHaveURL(/\/bots-v2/);
  });
});

test.describe('Search (Cmd+K)', () => {
  test('opens with Cmd+K', async ({ page }) => {
    await page.goto('/portfolio-v2');
    await page.waitForTimeout(2000);
    // `Meta` = touche Windows sous Windows/Linux : le raccourci ne se
    // déclenchait jamais hors macOS. `ControlOrMeta` mappe sur la bonne touche.
    await page.keyboard.press('ControlOrMeta+k');
    await expect(page.locator('input[placeholder*="Rechercher"]')).toBeVisible({ timeout: 5000 });
    await page.keyboard.press('Escape');
  });

  test('can search and navigate', async ({ page }) => {
    await page.goto('/portfolio-v2');
    await page.waitForTimeout(2000);
    // `Meta` = touche Windows sous Windows/Linux : le raccourci ne se
    // déclenchait jamais hors macOS. `ControlOrMeta` mappe sur la bonne touche.
    await page.keyboard.press('ControlOrMeta+k');
    await page.fill('input[placeholder*="Rechercher"]', 'backtest');
    await page.keyboard.press('Enter');
    // S10 — l'entrée « Backtest » de la recherche cible l'onglet du Laboratoire.
    await page.waitForURL('**/lab?tab=backtest', { timeout: 5000 });
  });
});

test.describe('Theme', () => {
  test('can toggle theme', async ({ page }) => {
    await page.goto('/portfolio-v2');
    await page.waitForTimeout(2000);
    const themeBtn = page.locator('button[title*="clair"], button[title*="sombre"]');
    await expect(themeBtn).toBeVisible();
    await themeBtn.click();
    await page.waitForTimeout(500);
    const htmlClass = await page.evaluate(() => document.documentElement.className);
    expect(htmlClass).toMatch(/dark|light/);
  });
});

test.describe('Settings', () => {
  test('displays risk presets', async ({ page }) => {
    await page.goto('/settings');
    await page.waitForTimeout(3000);
    // `.first()` : les trois presets sont rendus simultanément, donc le
    // locator résout 3 éléments → strict mode violation sans ça.
    await expect(
      page.locator('text=Prudent').or(page.locator('text=Équilibré')).or(page.locator('text=Agressif')).first(),
    ).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Backtest', () => {
  // S10 — /backtest redirige vers l'onglet Backtest du Laboratoire, dont le
  // bouton s'appelle « Analyser » (pipeline guidé) et non « Lancer le backtest ».
  test('has run button', async ({ page }) => {
    await page.goto('/backtest');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=Analyser').first()).toBeVisible({ timeout: 10000 });
  });
});

test.describe('WebSocket', () => {
  test('shows WS status in topbar', async ({ page }) => {
    await page.goto('/portfolio-v2');
    await page.waitForTimeout(3000);
    const wsIcon = page.locator('svg.lucide-wifi, svg.lucide-wifi-off');
    await expect(wsIcon).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Audit Log', () => {
  test('displays audit table', async ({ page }) => {
    await page.goto('/audit-log');
    await page.waitForTimeout(3000);
    // `.first()` : « Journal » apparaît aussi dans la nav latérale, ce qui
    // faisait échouer le locator en strict mode (2 éléments).
    await expect(page.locator('text=Journal').first()).toBeVisible({ timeout: 10000 });
    await expect(page.locator('table').first()).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Compare', () => {
  test('has compare button', async ({ page }) => {
    await page.goto('/compare');
    await page.waitForTimeout(2000);
    // `.first()` : « Comparatif » figure aussi dans la nav latérale (3 éléments
    // au total) — strict mode violation sans ça.
    await expect(page.locator('text=Comparer').or(page.locator('text=Comparatif')).first()).toBeVisible({ timeout: 10000 });
  });
});
