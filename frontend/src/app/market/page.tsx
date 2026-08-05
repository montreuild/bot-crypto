'use client';

/**
 * Page Marché — Scanner (avec Top opportunités intégré) / Smart Graph /
 * Smart Replay / Dérivés.
 */

import { Suspense, useState } from 'react';
import dynamic from 'next/dynamic';
import { useRouter, useSearchParams } from 'next/navigation';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Network, CandlestickChart, Film, TrendingUp } from 'lucide-react';
import { ScannerView } from '@/components/views/scanner-view';

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
  const requested = searchParams.get('tab');
  const initialTab = TABS.includes(requested as (typeof TABS)[number]) ? requested! : 'scanner';
  const [tab, setTab] = useState<string>(initialTab);

  const symbolParam = searchParams.get('symbol') ?? undefined;
  const tfParam = searchParams.get('tf') ?? undefined;

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-end justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Marché</h1>
          <p className="text-sm text-muted mt-1">
            Scanner &amp; opportunités, Smart Graph, Replay et dérivés
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
          {/* Vue unique : scanner + top opportunités (plus de layout 2/3 + colonne vide) */}
          <ScannerView
            initialSymbol={symbolParam}
            initialTf={tfParam}
            onAnalyze={(symbol, tf) =>
              router.push(`/lab?tab=backtest&symbol=${encodeURIComponent(symbol)}&tf=${tf}`)
            }
          />
        </TabsContent>
        <TabsContent value="smartgraph">
          <SmartGraphView initialSymbol={symbolParam} initialTf={tfParam} />
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
