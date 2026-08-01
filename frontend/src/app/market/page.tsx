'use client';

/**
 * S6-F4-US1 — Page Marché (fusion Scanner + Smart Graph + Smart Replay + Dérivés).
 *
 * Tabs : Scanner / Smart Graph / Smart Replay / Dérivés
 *
 * Lot Marché — les quatre onglets montent désormais le contenu réel des
 * anciennes pages (`src/components/views/*`) et non plus des cartes de renvoi.
 * `/scanner`, `/smartgraph`, `/smartreplay` et `/derivatives` sont en 308 vers
 * `/market?tab=…` (cf. `next.config.mjs` et docs/audit-ui-ux-bot-crypto.md).
 *
 * L'onglet est piloté par `?tab=` pour que les 308 atterrissent au bon endroit
 * et que les liens profonds restent partageables.
 */

import { Suspense, useState } from 'react';
import dynamic from 'next/dynamic';
import { useRouter, useSearchParams } from 'next/navigation';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Network, CandlestickChart, Film, TrendingUp } from 'lucide-react';
import { OpportunitiesWidget } from '@/components/cards/opportunities-widget';
import { ScannerView } from '@/components/views/scanner-view';

/*
  Les trois onglets graphiques tirent `lightweight-charts` ou `recharts`. Montés
  en import statique, ils faisaient passer le premier chargement de /market de
  ~110 kB à 333 kB — payés même par qui n'ouvre que le Scanner, alors que Radix
  ne monte que l'onglet actif. `dynamic` aligne le coût réseau sur ce
  comportement : le chunk part au clic sur l'onglet.
  `ssr: false` : les deux vues SMC instancient le chart contre le DOM.
*/
const loading = () => <div className="p-8 text-center text-sm text-muted">Chargement…</div>;

const SmartGraphView = dynamic(
  () => import('@/components/views/smart-graph-view').then((m) => m.SmartGraphView),
  { ssr: false, loading },
);
const SmartReplayView = dynamic(
  () => import('@/components/views/smart-replay-view').then((m) => m.SmartReplayView),
  { ssr: false, loading },
);
const DerivativesView = dynamic(
  () => import('@/components/views/derivatives-view').then((m) => m.DerivativesView),
  { ssr: false, loading },
);

const TABS = ['scanner', 'smartgraph', 'smartreplay', 'derivatives'] as const;

export default function MarketPage() {
  return (
    <Suspense fallback={<div className="p-6 text-muted">Chargement…</div>}>
      <MarketContent />
    </Suspense>
  );
}

function MarketContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Un `?tab=` inconnu (ancien favori, faute de frappe) retombe sur Scanner
  // plutôt que d'afficher un contenu vide.
  const requested = searchParams.get('tab');
  const initialTab = TABS.includes(requested as (typeof TABS)[number]) ? requested! : 'scanner';
  const [tab, setTab] = useState<string>(initialTab);

  // `/scanner?symbol=X&tf=Y` (lien « Analyser » de /data) est redirigé en 308
  // vers `/market?tab=scanner&symbol=X&tf=Y` : Next conserve la query. On la
  // transmet à la vue pour que le formulaire arrive pré-rempli.
  const symbolParam = searchParams.get('symbol') ?? undefined;
  const tfParam = searchParams.get('tf') ?? undefined;

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-end justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Marché</h1>
          <p className="text-sm text-muted mt-1">
            Scanner, analyse SMC/ICT, replay et dérivés — vue unifiée du marché
          </p>
        </div>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid grid-cols-4 w-full max-w-xl">
          <TabsTrigger value="scanner">
            <Network className="w-3.5 h-3.5 mr-1.5" />
            Scanner
          </TabsTrigger>
          <TabsTrigger value="smartgraph">
            <CandlestickChart className="w-3.5 h-3.5 mr-1.5" />
            Smart Graph
          </TabsTrigger>
          <TabsTrigger value="smartreplay">
            <Film className="w-3.5 h-3.5 mr-1.5" />
            Smart Replay
          </TabsTrigger>
          <TabsTrigger value="derivatives">
            <TrendingUp className="w-3.5 h-3.5 mr-1.5" />
            Dérivés
          </TabsTrigger>
        </TabsList>

        <TabsContent value="scanner">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 items-start">
            <div className="lg:col-span-2">
              <ScannerView
                initialSymbol={symbolParam}
                initialTf={tfParam}
                onAnalyze={(symbol, tf) =>
                  router.push(`/lab?tab=backtest&symbol=${encodeURIComponent(symbol)}&tf=${tf}`)
                }
              />
            </div>
            {/* S8-F4-US2 — Top opportunités */}
            <OpportunitiesWidget timeframe="1h" limit={10} />
          </div>
        </TabsContent>
        <TabsContent value="smartgraph">
          <SmartGraphView />
        </TabsContent>
        <TabsContent value="smartreplay">
          <SmartReplayView />
        </TabsContent>
        <TabsContent value="derivatives">
          <DerivativesView />
        </TabsContent>
      </Tabs>
    </div>
  );
}
