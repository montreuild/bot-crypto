'use client';

/**
 * P0-3 — Éditeur de strategy_params.
 *
 * La route `POST /api/config/strategy-params` et le hook `useSetStrategyParams`
 * existent mais aucun composant UI ne les appelait.
 */

import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Settings, Save, RotateCcw, Loader2 } from 'lucide-react';

export function StrategyParamsEditor({
  strategyName,
  params,
  onSave,
}: {
  strategyName: string;
  params: Record<string, any>;
  onSave: (strategy: string, params: Record<string, any>) => Promise<void>;
}) {
  const qc = useQueryClient();
  const [editedParams, setEditedParams] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);

  useEffect(() => {
    setEditedParams({ ...params });
    setHasChanges(false);
  }, [params]);

  const handleChange = (key: string, value: string) => {
    const original = params[key];
    const isNumber = typeof original === 'number';
    const newVal = isNumber ? Number(value) : value;
    setEditedParams((prev) => ({ ...prev, [key]: newVal }));
    setHasChanges(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(strategyName, editedParams);
      toast.success(`Paramètres de ${strategyName} sauvegardés`);
      setHasChanges(false);
      qc.invalidateQueries({ queryKey: ['config'] });
    } catch (e: any) {
      toast.error(`Erreur : ${e.message}`);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setEditedParams({ ...params });
    setHasChanges(false);
  };

  const paramEntries = Object.entries(editedParams).filter(
    ([, v]) => typeof v === 'number' || typeof v === 'string',
  );

  if (paramEntries.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <Settings className="w-4 h-4" />
            Paramètres — {strategyName}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-muted">Aucun paramètre éditable pour cette stratégie.</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm">
            <Settings className="w-4 h-4" />
            Paramètres — {strategyName}
          </span>
          {hasChanges && (
            <Badge variant="warning" className="text-[10px]">Modifié</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {paramEntries.map(([key, value]) => (
            <div key={key} className="space-y-1">
              <Label htmlFor={`param-${key}`} className="text-[10px] font-mono text-dim">
                {key}
              </Label>
              <Input
                id={`param-${key}`}
                type={typeof value === 'number' ? 'number' : 'text'}
                value={value}
                step={typeof value === 'number' ? 'any' : undefined}
                onChange={(e) => handleChange(key, e.target.value)}
                className="font-mono text-sm"
              />
            </div>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="primary" onClick={handleSave} disabled={!hasChanges || saving}>
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            Sauvegarder
          </Button>
          <Button size="sm" variant="ghost" onClick={handleReset} disabled={!hasChanges || saving}>
            <RotateCcw className="w-3.5 h-3.5" />
            Annuler
          </Button>
        </div>
        <p className="text-[10px] text-dim">
          ⚠ Ces paramètres écrasent ceux de{' '}
          <code className="text-primary-600">strategies/{strategyName}.yaml</code>.
          L&apos;optimiseur peut aussi les modifier via{' '}
          <code className="text-primary-600">optimizer_results</code>.
        </p>
      </CardContent>
    </Card>
  );
}
