'use client';

/**
 * S6-F4-US1 — Page Marché (fusion Scanner + Smart Graph + Smart Replay + Dérivés).
 *
 * Stratégie strangler fig : coexiste avec les pages existantes.
 *
 * Tabs : Scanner / Smart Graph / Smart Replay / Dérivés
 *
 * Le scanner est enrichi d'un lien « Analyser cette paire au Laboratoire »
 * qui redirige vers /lab?tab=backtest&symbol=X&tf=Y.
 */

import { useState } from 'react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Network, CandlestickChart, Film, TrendingUp, ArrowRight } from 'lucide-react';
import { useRouter } from 'next/navigation';

export default function MarketPage() {
  const router = useRouter();
  const [tab, setTab] = useState('scanner');

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
          <MarketScannerTab onAnalyze={(symbol, tf) => router.push(`/lab?tab=backtest&symbol=${encodeURIComponent(symbol)}&tf=${tf}`)} />
        </TabsContent>
        <TabsContent value="smartgraph">
          <RedirectCard href="/smartgraph" title="Smart Graph (SMC)" description="Chart candlestick avec 14 overlays SMC : OB, FVG, liquidity pools, breakers, structure, premium/discount, signal entry/SL/TP." />
        </TabsContent>
        <TabsContent value="smartreplay">
          <RedirectCard href="/smartreplay" title="Smart Replay (SMC)" description="Rejeu bougie par bougie avec calques SMC lifecycle. Contrôles play/pause/speed + slider scrubbing." />
        </TabsContent>
        <TabsContent value="derivatives">
          <RedirectCard href="/derivatives" title="Dérivés" description="Funding rate, Open Interest, Long/Short ratio, Taker buy/sell ratio. 4 charts avec sélecteur de période." />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── Scanner Tab (placeholder avec lien vers /scanner) ─────────────────────

function MarketScannerTab({ onAnalyze }: { onAnalyze: (symbol: string, tf: string) => void }) {
  return (
    <Card>
      <CardContent className="p-6 space-y-4">
        <div className="flex items-start gap-3">
          <Network className="w-8 h-8 text-primary-400 flex-shrink-0" />
          <div className="flex-1">
            <h3 className="text-base font-semibold mb-1">Scanner de marché</h3>
            <p className="text-sm text-muted">
              Screen multi-symboles avec filtres (régime, ADX, ATR%, RSI). Top opportunités
              par score combiné 40% volume 24h + 60% ATR%. Lien direct vers Laboratoire pour
              analyser une paire.
            </p>
          </div>
        </div>

        {/* Démo : quelques paires cliquables */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 pt-2">
          {['BTC/USDC', 'ETH/USDC', 'XRP/USDC', 'SOL/USDC'].map((s) => (
            <Button
              key={s}
              variant="outline"
              size="sm"
              onClick={() => onAnalyze(s, '1h')}
              className="justify-between font-mono"
            >
              {s}
              <ArrowRight className="w-3 h-3" />
            </Button>
          ))}
        </div>

        <div className="pt-3 border-t border-border">
          <Button variant="ghost" onClick={() => window.location.href = '/scanner'}>
            Aller au scanner complet
            <ArrowRight className="w-4 h-4 ml-1" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function RedirectCard({ href, title, description }: { href: string; title: string; description: string }) {
  return (
    <Card>
      <CardContent className="p-8 text-center">
        <h3 className="text-base font-semibold mb-1">{title}</h3>
        <p className="text-sm text-muted max-w-md mx-auto mb-4">{description}</p>
        <Button variant="outline" onClick={() => window.location.href = href}>
          Ouvrir {title}
          <ArrowRight className="w-4 h-4 ml-1" />
        </Button>
      </CardContent>
    </Card>
  );
}
