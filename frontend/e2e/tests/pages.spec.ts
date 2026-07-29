import { test, expect } from '@playwright/test';

const PAGES = [
  { path: '/dashboard', title: 'Dashboard' },
  { path: '/bots', title: 'Bots' },
  { path: '/trades', title: 'Trades' },
  { path: '/portfolio', title: 'Portefeuille' },
  { path: '/backtest', title: 'Backtest' },
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
// Elle doit rediriger vers /dashboard — sinon les redirects 308 du backend
// (`HTML_ROUTES_TO_REDIRECT`) pointent vers une page morte.
test.describe('Racine', () => {
  test('/ redirige vers /dashboard', async ({ page }) => {
    await page.goto('/');
    await page.waitForURL('**/dashboard', { timeout: 10000 });
    await expect(page).toHaveURL(/\/dashboard/);
  });
});

test.describe('Dashboard', () => {
  test('displays KPIs', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForTimeout(3000);
    const kpi = page.locator('text=Capital').or(page.locator('text=PnL')).or(page.locator('text=Win Rate'));
    await expect(kpi.first()).toBeVisible({ timeout: 10000 });
  });

  test('has equity curve chart', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForTimeout(3000);
    const chart = page.locator('.recharts-surface').or(page.locator('svg'));
    await expect(chart.first()).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Navigation', () => {
  test('sidebar has nav groups', async ({ page }) => {
    await page.goto('/dashboard');
    await expect(page.locator('text=Trading').first()).toBeVisible();
    await expect(page.locator('text=Recherche').first()).toBeVisible();
  });

  test('can navigate to Bots', async ({ page }) => {
    await page.goto('/dashboard');
    await page.click('a[href="/bots"]');
    await page.waitForURL('**/bots');
    await expect(page).toHaveURL(/\/bots/);
  });
});

test.describe('Search (Cmd+K)', () => {
  test('opens with Cmd+K', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForTimeout(2000);
    await page.keyboard.press('Meta+K');
    await expect(page.locator('input[placeholder*="Rechercher"]')).toBeVisible({ timeout: 5000 });
    await page.keyboard.press('Escape');
  });

  test('can search and navigate', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForTimeout(2000);
    await page.keyboard.press('Meta+K');
    await page.fill('input[placeholder*="Rechercher"]', 'backtest');
    await page.keyboard.press('Enter');
    await page.waitForURL('**/backtest', { timeout: 5000 });
  });
});

test.describe('Theme', () => {
  test('can toggle theme', async ({ page }) => {
    await page.goto('/dashboard');
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
    await expect(page.locator('text=Prudent').or(page.locator('text=Équilibré')).or(page.locator('text=Agressif'))).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Backtest', () => {
  test('has run button', async ({ page }) => {
    await page.goto('/backtest');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=Lancer le backtest')).toBeVisible({ timeout: 10000 });
  });
});

test.describe('WebSocket', () => {
  test('shows WS status in topbar', async ({ page }) => {
    await page.goto('/dashboard');
    await page.waitForTimeout(3000);
    const wsIcon = page.locator('svg.lucide-wifi, svg.lucide-wifi-off');
    await expect(wsIcon).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Audit Log', () => {
  test('displays audit table', async ({ page }) => {
    await page.goto('/audit-log');
    await page.waitForTimeout(3000);
    await expect(page.locator('text=Journal')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('table')).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Compare', () => {
  test('has compare button', async ({ page }) => {
    await page.goto('/compare');
    await page.waitForTimeout(2000);
    await expect(page.locator('text=Comparer').or(page.locator('text=Comparatif'))).toBeVisible({ timeout: 10000 });
  });
});
