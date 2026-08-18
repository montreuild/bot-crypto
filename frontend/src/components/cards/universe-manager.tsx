'use client';

/**
 * Gestionnaire d'univers.
 *
 * GET /api/universe, GET /api/universe/{name}, POST/DELETE symbols.
 * Pas de comptage de bougies ici (voir page Données OHLCV).
 * À l'ajout d'un ticker, l'API lance un backfill yfinance multi-TF en fond.
 */

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { toast } from 'sonner';
import { errorMessage } from '@/lib/utils';
import type { UniverseMember } from '@/types';
import { useState } from 'react';
import { Database, Plus, Trash2, ChevronRight, Loader2 } from 'lucide-react';

export function UniverseManager() {
  const qc = useQueryClient();
  const [selectedUniverse, setSelectedUniverse] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newSymbol, setNewSymbol] = useState('');
  const [newName, setNewName] = useState('');
  const [removeTarget, setRemoveTarget] = useState<{ universe: string; symbol: string } | null>(null);

  const universesQuery = useQuery({
    queryKey: ['universes'],
    queryFn: api.getUniverses,
    refetchInterval: 60000,
  });

  const universeDetailQuery = useQuery({
    queryKey: ['universe', selectedUniverse],
    queryFn: () => api.getUniverse(selectedUniverse!),
    enabled: !!selectedUniverse,
    refetchInterval: 120000,
    staleTime: 60_000,
    retry: 1,
  });

  const members = (universeDetailQuery.data?.members ?? []) as UniverseMember[];

  const addSymbol = useMutation({
    mutationFn: ({ universe, body }: { universe: string; body: { symbol: string; name?: string } }) =>
      api.addUniverseSymbol(universe, body),
    onSuccess: (res: { backfill?: { status?: string } }) => {
      qc.invalidateQueries({ queryKey: ['universe', selectedUniverse] });
      qc.invalidateQueries({ queryKey: ['universes'] });
      qc.invalidateQueries({ queryKey: ['dataStatus'] });
      const bf = res?.backfill;
      toast.success(
        bf?.status === 'started'
          ? 'Symbole ajouté — backfill max multi-TF démarré (voir Données OHLCV)'
          : 'Symbole ajouté (YAML : commentaires préservés)',
      );
      setShowAddForm(false);
      setNewSymbol('');
      setNewName('');
    },
    onError: (e) => toast.error(`Erreur : ${errorMessage(e)}`),
  });

  const removeSymbol = useMutation({
    mutationFn: ({ universe, symbol }: { universe: string; symbol: string }) =>
      api.removeUniverseSymbol(universe, symbol),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['universe', selectedUniverse] });
      toast.success('Symbole retiré');
      setRemoveTarget(null);
    },
    onError: (e) => toast.error(`Erreur : ${errorMessage(e)}`),
  });

  const handleAdd = () => {
    if (!selectedUniverse || !newSymbol) return;
    addSymbol.mutate({
      universe: selectedUniverse,
      body: {
        symbol: newSymbol.trim(),
        name: newName.trim() || undefined,
      },
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <Database className="w-4 h-4 text-primary-400" />
          Univers d&apos;instruments
        </CardTitle>
      </CardHeader>
      <CardContent>
        {!selectedUniverse && (
          <div className="space-y-2">
            {universesQuery.isLoading ? (
              <div className="flex items-center justify-center py-4">
                <Loader2 className="w-5 h-5 animate-spin text-muted" />
              </div>
            ) : universesQuery.isError ? (
              <p className="text-xs text-red-400">
                Impossible de charger les univers : {(universesQuery.error as Error)?.message}
              </p>
            ) : universesQuery.data?.universes?.length === 0 ? (
              <p className="text-xs text-muted">Aucun univers configuré</p>
            ) : (
              universesQuery.data?.universes?.map((u) => (
                <button
                  key={u.id || u.label}
                  onClick={() => setSelectedUniverse(u.id || u.label || null)}
                  className="w-full flex items-center gap-3 p-3 rounded-md border border-border hover:border-border-hi hover:bg-card-hover transition-colors text-left"
                >
                  <div className="flex-1 min-w-0">
                    <div className="font-mono text-sm font-semibold">{u.id || u.label}</div>
                    <div className="text-[10px] text-muted mt-0.5">
                      {u.venue} · {u.asset_class} · {u.quote_currency}
                    </div>
                  </div>
                  <div className="text-right">
                    <Badge variant="info">{u.n_symbols} symboles</Badge>
                    {u.verified ? (
                      <Badge variant="success" className="ml-1 text-[9px]">vérifié</Badge>
                    ) : (
                      <Badge variant="warning" className="ml-1 text-[9px]">à vérifier</Badge>
                    )}
                  </div>
                  <ChevronRight className="w-4 h-4 text-dim" />
                </button>
              ))
            )}
          </div>
        )}

        {selectedUniverse && (
          <div className="space-y-3">
            <button
              onClick={() => {
                setSelectedUniverse(null);
                setShowAddForm(false);
              }}
              className="text-xs text-muted hover:text-foreground transition-colors"
            >
              ← Retour à la liste
            </button>

            <div className="flex items-center justify-between">
              <div>
                <div className="font-mono text-sm font-semibold">{selectedUniverse}</div>
                <div className="text-[10px] text-muted">
                  {universeDetailQuery.data?.as_of && `As of ${universeDetailQuery.data.as_of}`}
                  {' · '}
                  {members.length} symboles
                </div>
              </div>
              <Button size="sm" variant="outline" onClick={() => setShowAddForm(!showAddForm)}>
                <Plus className="w-3 h-3" />
                Ajouter
              </Button>
            </div>

            {showAddForm && (
              <div className="p-3 border border-border rounded-md space-y-2 bg-card-hover">
                <div>
                  <Label htmlFor="univ-symbol">Symbole yfinance *</Label>
                  <Input
                    id="univ-symbol"
                    value={newSymbol}
                    onChange={(e) => setNewSymbol(e.target.value)}
                    placeholder="ALO.PA"
                    className="font-mono text-xs"
                  />
                  <p className="text-[10px] text-muted mt-1">
                    Ticker <strong className="text-dim">yfinance</strong> complet
                    (ex. <code className="font-mono">ALO.PA</code>). À l&apos;ajout, un
                    backfill max est lancé pour 15m / 30m / 1h / 4h / 1d (voir Données OHLCV).
                  </p>
                </div>
                <div>
                  <Label htmlFor="univ-name">Nom</Label>
                  <Input
                    id="univ-name"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="Alstom"
                    className="text-xs"
                  />
                </div>
                <Button
                  size="sm"
                  onClick={handleAdd}
                  disabled={!newSymbol || addSymbol.isPending}
                  className="w-full"
                >
                  {addSymbol.isPending && <Loader2 className="w-3 h-3 animate-spin" />}
                  Ajouter le symbole
                </Button>
              </div>
            )}

            {universeDetailQuery.isLoading ? (
              <div className="flex flex-col items-center justify-center py-6 gap-2">
                <Loader2 className="w-5 h-5 animate-spin text-muted" />
                <span className="text-[10px] text-muted">Chargement des membres…</span>
              </div>
            ) : universeDetailQuery.isError ? (
              <p className="text-xs text-red-400">
                Erreur : {(universeDetailQuery.error as Error)?.message}
              </p>
            ) : members.length === 0 ? (
              <p className="text-xs text-muted">Univers vide</p>
            ) : (
              <div className="border border-border rounded-md overflow-hidden max-h-80 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="bg-card-hover sticky top-0">
                    <tr className="text-left text-dim border-b border-border">
                      <th scope="col" className="p-2 font-medium">Symbole</th>
                      <th scope="col" className="p-2 font-medium">Nom</th>
                      <th scope="col" className="p-2 font-medium w-8" />
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((m) => (
                      <tr key={m.symbol} className="border-b border-border/50 hover:bg-card-hover">
                        <td className="p-2 font-mono font-semibold">{m.symbol}</td>
                        <td className="p-2 text-muted truncate max-w-[180px]">{m.name || '—'}</td>
                        <td className="p-2">
                          <button
                            onClick={() => setRemoveTarget({ universe: selectedUniverse, symbol: m.symbol ?? '' })}
                            className="text-dim hover:text-red-400 transition-colors"
                            aria-label={`Retirer ${m.symbol}`}
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        <ConfirmDialog
          open={!!removeTarget}
          onOpenChange={(open) => !open && setRemoveTarget(null)}
          title="Retirer ce symbole ?"
          description={`Le symbole ${removeTarget?.symbol} sera retiré de l'univers ${removeTarget?.universe}. Les données en cache ne sont pas affectées.`}
          confirmLabel="Retirer"
          variant="danger"
          isLoading={removeSymbol.isPending}
          onConfirm={() => {
            if (removeTarget) removeSymbol.mutate(removeTarget);
          }}
        />
      </CardContent>
    </Card>
  );
}
