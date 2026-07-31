import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [
    ['html', { outputFolder: 'playwright-report' }],
    ['list'],
  ],
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    timeout: 30000,
    actionTimeout: 15000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    // S2-F3-US1 — Tests multi-viewports pour vérifier le responsive
    {
      name: 'mobile-chromium',
      use: { ...devices['Pixel 5'] },
    },
    {
      // ⚠ `devices['iPad (gen 7)']` porte `defaultBrowserType: 'webkit'`. Le
      // projet s'appelait pourtant `tablet-chromium`, et la CI n'installe que
      // chromium : **tous** les tests de ce projet échouaient instantanément
      // sur « browserType.launch: Executable doesn't exist … webkit-2336 ».
      // Le but affiché est de vérifier le responsive à une largeur tablette :
      // on garde la géométrie de l'iPad et on force le moteur chromium, ce qui
      // rend le projet exécutable sans installer WebKit en CI.
      // Contrepartie assumée : les bugs de rendu propres à WebKit ne sont pas
      // couverts. Les couvrir demanderait `playwright install webkit` dans les
      // jobs e2e/a11y/visual.
      name: 'tablet-chromium',
      use: { ...devices['iPad (gen 7)'], defaultBrowserType: 'chromium' },
    },
  ],
});
