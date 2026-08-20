/**
 * S2-F3-US7 — Configuration Vitest + React Testing Library.
 *
 * Tests unitaires pour les composants critiques :
 *   - Button, Card, Badge
 *   - KPICard (flash-on-change)
 *   - EquityCurve (data mapping)
 *   - QueryBoundary (loading/error/empty states)
 *
 * Pour exécuter : npx vitest run
 * Watch mode : npx vitest
 * Coverage : npx vitest run --coverage
 */

import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'src/**/*.spec.{ts,tsx}'],
    exclude: ['node_modules', 'e2e'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['src/components/**', 'src/lib/**', 'src/hooks/**'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/**/*.spec.{ts,tsx}'],
      thresholds: {
        // TEST-02 : plancher réel (mesuré ~5 % avant cette revue).
        // Objectif 30 % par paliers ; un seuil trop haut ferait échouer
        // la CI sans couvrir davantage lib/ et hooks/.
        statements: 5,
        branches: 20,
        functions: 10,
        lines: 5,
      },
    },
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, './src'),
    },
  },
});
