'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { Search, Loader2, TrendingUp, Activity } from 'lucide-react';

export default function ScannerPage() {
  const [symbol, setSymbol] = useState('BTC/USDC');
  const [tf, setTf] = useState('1h');
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const handleScan = async () => {
    setLoading(true);
    try {
      const r = await api.fastAnalysis(symbol, tf);
      setResult(r);
      toast.success(`Analyse terminée pour ${symbol} ${tf}`);
    } catch (e: any) {
      toast.error(`Erreur: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Scanner</h1>
        <p className="text-sm text-muted mt-1">
          Fast Analyse SMC/ICT — détectez les setups en temps réel
        </p>
      </div>

      <Card>
        <CardHeader><CardTitle>Configuration</CardTitle></CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="text-xs text-dim block mb-1.5">Symbole</label>
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
              />
            </div>
            <div>
              <label className="text-xs text-dim block mb-1.5">Timeframe</label>
              <select
                value={tf}
                onChange={(e) => setTf(e.target.value)}
                className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm"
              >
                {['5m', '15m', '1h', '4h', '1d'].map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div className="flex items-end">
              <Button onClick={handleScan} disabled={loading} variant="primary" className="w-full">
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                Lancer
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {result && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Tendance</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-3">
                <TrendingUp className="w-8 h-8 text-emerald-400" />
                <div>
                  <div className="font-semibold capitalize">{result.trend || 'Neutral'}</div>
                  <div className="text-xs text-muted">{result.trend_strength?.toFixed(2) || '—'} strength</div>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Volatilité (ATR)</CardTitle></CardHeader>
            <CardContent>
              <div className="font-mono text-2xl font-semibold">
                {result.atr?.toFixed(2) || '—'}
              </div>
              <div className="text-xs text-muted mt-1">
                {result.atr_pct?.toFixed(2) || '—'}% du prix
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Volume</CardTitle></CardHeader>
            <CardContent>
              <div className="font-mono text-2xl font-semibold">
                {(result.volume || 0).toLocaleString()}
              </div>
              <div className="text-xs text-muted mt-1">Dernière bougie</div>
            </CardContent>
          </Card>

          {result.patterns && result.patterns.length > 0 && (
            <Card className="md:col-span-2 lg:col-span-3">
              <CardHeader>
                <CardTitle>Patterns détectés</CardTitle>
                <Badge variant="info">{result.patterns.length}</Badge>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                  {result.patterns.map((p: any, i: number) => (
                    <div key={i} className="p-3 rounded-lg bg-card-hover border border-border">
                      <div className="text-xs text-dim uppercase">{p.type}</div>
                      <div className="font-semibold text-sm mt-1">{p.name}</div>
                      {p.confidence && (
                        <div className="text-xs text-emerald-400 mt-1">
                          {(p.confidence * 100).toFixed(0)}% confidence
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
