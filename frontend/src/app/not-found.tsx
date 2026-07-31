/**
 * S1-F3-US8 — Page 404 personnalisée.
 *
 * Remplace la 404 par défaut de Next.js par une page soignée qui propose
 * des liens vers les pages principales.
 */

import Link from 'next/link';
import { Home, Bot, LineChart, Settings } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[80vh] px-4 text-center">
      <div className="text-[120px] font-bold font-mono text-primary-400/30 leading-none">
        404
      </div>
      <h1 className="text-2xl font-semibold mt-4 mb-2">Page introuvable</h1>
      <p className="text-muted mb-8 max-w-md">
        La page que vous cherchez n&apos;existe pas ou a été déplacée.
        Vérifiez l&apos;URL ou revenez à une page connue.
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 max-w-2xl w-full">
        {/* S10 — cibles v2 directes : /dashboard, /bots et /backtest sont en 308. */}
        <Link
          href="/portfolio-v2"
          className="flex flex-col items-center gap-2 p-4 rounded-lg border border-border bg-card hover:border-primary-400/50 hover:bg-card-hover transition-colors"
        >
          <Home className="w-5 h-5 text-primary-400" />
          <span className="text-sm">Portefeuille</span>
        </Link>
        <Link
          href="/bots-v2"
          className="flex flex-col items-center gap-2 p-4 rounded-lg border border-border bg-card hover:border-primary-400/50 hover:bg-card-hover transition-colors"
        >
          <Bot className="w-5 h-5 text-primary-400" />
          <span className="text-sm">Mes Bots</span>
        </Link>
        <Link
          href="/lab?tab=backtest"
          className="flex flex-col items-center gap-2 p-4 rounded-lg border border-border bg-card hover:border-primary-400/50 hover:bg-card-hover transition-colors"
        >
          <LineChart className="w-5 h-5 text-primary-400" />
          <span className="text-sm">Backtest</span>
        </Link>
        <Link
          href="/settings"
          className="flex flex-col items-center gap-2 p-4 rounded-lg border border-border bg-card hover:border-primary-400/50 hover:bg-card-hover transition-colors"
        >
          <Settings className="w-5 h-5 text-primary-400" />
          <span className="text-sm">Réglages</span>
        </Link>
      </div>
    </div>
  );
}
