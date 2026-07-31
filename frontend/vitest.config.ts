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
        statements: 60,
        branches: 60,
        functions: 60,
        lines: 60,
      },
    },
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, './src'),
    },
  },
});
