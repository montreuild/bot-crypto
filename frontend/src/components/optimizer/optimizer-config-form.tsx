'use client';

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { cn, errorMessage } from '@/lib/utils';
import { toast } from 'sonner';
import { useStartOptimize, useConfig, useOptimizeBudget } from '@/hooks/use-api';
import { Play, Loader2, CheckCircle2, Sparkles, Info } from 'lucide-react';
import type { OptimizeSpaces } from '@/types';
import { useTradingTimeframes } from '@/hooks/use-trading-timeframes';
import { TimeframeButtons } from '@/components/ui/timeframe-select';
import { isOosHint } from '@/lib/limit-hint';
import { METHODS, FALLBACK_SYMBOLS, PRESETS, type PresetKey } from '@/components/optimizer/status';
import { StrategyPicker } from '@/components/ui/strategy-picker';

type LaunchFeedback =
  | { kind: 'fetching' }
  | { kind: 'ok'; nJobs: number; receivedBars?: Record<string, number>; skipped?: unknown[] }
  | null;

export function OptimizerConfigForm({
  filterMl,
  spaces,
}: {
  filterMl: boolean;
  spaces: OptimizeSpaces | undefined;
}) {
  const startOptimize = useStartOptimize();
  const { timeframes: activeTfs, defaultTf } = useTradingTimeframes('1h');
  const searchParams = useSearchParams();
  const { data: configData } = useConfig();

  const allStrategies = useMemo(() => (spaces ? Object.keys(spaces) : []), [spaces]);
  const visibleStrategies = useMemo(() => {
    if (!filterMl) return allStrategies;
    return allStrategies.filter((s) => !!(spaces?.[s]?.is_ml));
  }, [allStrategies, filterMl, spaces]);

  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);
  const [selectedTfs, setSelectedTfs] = useState<string[]>([]);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(['BTC/USDC']);
  const [method, setMethod] = useState<(typeof METHODS)[number]>('bayesian');
  const [nTrials, setNTrials] = useState(60);
  const [nJobs, setNJobs] = useState(1);
  const [autoApply, setAutoApply] = useState(false);
  const [paramSearchOptim, setParamSearchOptim] = useState(true);
  const [activePreset, setActivePreset] = useState<PresetKey | null>(null);
  const [earlyStopping, setEarlyStopping] = useState(0);
  const [limitPerTf, setLimitPerTf] = useState(0);
  const [mlTuneHp, setMlTuneHp] = useState(false);
  const [launchFeedback, setLaunchFeedback] = useState<LaunchFeedback>(null);

  const availableSymbols = useMemo(() => {
    const scannerSymbols = configData?.scanner?.symbols;
    if (Array.isArray(scannerSymbols) && scannerSymbols.length > 0) return scannerSymbols;
    return FALLBACK_SYMBOLS;
  }, [configData]);

  function applyPreset(key: PresetKey) {
    const p = PRESETS[key];
    setNTrials(p.nTrials);
    setNJobs(p.nJobs);
    setEarlyStopping(p.earlyStopping);
    setMlTuneHp(p.mlTuneHp);
    setActivePreset(key);
  }
  function markCustom() {
    if (activePreset !== null) setActivePreset(null);
  }

  useEffect(() => {
    if (selectedStrategies.length > 0) return;
    const wanted = searchParams.get('strategy');
    if (wanted && visibleStrategies.includes(wanted)) {
      setSelectedStrategies([wanted]);
    }
  }, [visibleStrategies, selectedStrategies.length, searchParams]);

  useEffect(() => {
    if (activeTfs.length > 0 && selectedTfs.length === 0) {
      setSelectedTfs(activeTfs.includes(defaultTf) ? [defaultTf] : [activeTfs[0]]);
    }
  }, [activeTfs, defaultTf, selectedTfs.length]);

  const toggle = (list: string[], value: string, setter: (v: string[]) => void) => {
    setter(list.includes(value) ? list.filter((x) => x !== value) : [...list, value]);
  };

  const hasMlSelected = selectedStrategies.some((s) => spaces?.[s]?.is_ml);
  const budget = useOptimizeBudget(nTrials, selectedStrategies);
  const hasOmnibusSelected = selectedStrategies.some((s) => /omnibus/i.test(s));

  const listedStrategies = filterMl
    ? visibleStrategies
    : (allStrategies.length > 0
      ? allStrategies
      : ['pullback_trend', 'trend_rider', 'breakout', 'smart_money']);

  const handleStart = async () => {
    if (selectedStrategies.length === 0) {
      toast.error('Sélectionnez au moins une stratégie');
      return;
    }
    if (selectedTfs.length === 0) {
      toast.error('Sélectionnez au moins un timeframe');
      return;
    }
    if (selectedSymbols.length === 0) {
      toast.error('Sélectionnez au moins un symbole');
      return;
    }
    setLaunchFeedback({ kind: 'fetching' });
    try {
      const res = await startOptimize.mutateAsync({
        strategies: selectedStrategies.join(','),
        timeframes: selectedTfs.join(','),
        symbols: selectedSymbols.join(','),
        method,
        n_trials: nTrials,
        n_jobs: nJobs,
        auto_apply: autoApply,
        param_search_optim: paramSearchOptim,
        early_stop_patience: earlyStopping,
        limit: limitPerTf > 0 ? limitPerTf : undefined,
        ml_tune_hp: hasMlSelected ? mlTuneHp : undefined,
      });
      const nCreated = typeof res?.n_jobs_created === 'number'
        ? res.n_jobs_created
        : Array.isArray(res?.job_ids) ? res.job_ids.length : 0;
      setLaunchFeedback({
        kind: 'ok',
        nJobs: nCreated,
        receivedBars: res?.received_bars,
        skipped: res?.skipped,
      });
      toast.success(`${nCreated} job(s) lancé(s)`);
    } catch (e) {
      setLaunchFeedback(null);
      toast.error(`Erreur: ${errorMessage(e)}`);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Configuration</CardTitle>
        <Sparkles className="w-4 h-4 text-primary-400" />
      </CardHeader>
      <CardContent className="space-y-5">
        <StrategyPicker
          strategies={listedStrategies}
          value={selectedStrategies}
          onChange={setSelectedStrategies}
          spaces={spaces}
          selectedTfs={selectedTfs}
        />
        {filterMl && visibleStrategies.length === 0 && (
          <p className="text-[11px] text-muted italic mt-2">
            Aucune stratégie ML déclarée dans <code className="font-mono">/optimize/spaces</code>.
          </p>
        )}

        <div>
          <div className="text-xs text-dim mb-2">Timeframes (multi)</div>
          <TimeframeButtons multi values={selectedTfs} onChangeMulti={setSelectedTfs} />
          {limitPerTf > 0 && selectedTfs.length > 0 && (
            <div className="mt-2 space-y-0.5">
              {selectedTfs.map((tf) => {
                const h = isOosHint(limitPerTf, tf);
                return (
                  <div
                    key={tf}
                    className={cn('text-[10px] font-mono', h.capped ? 'text-amber-400' : 'text-dim')}
                  >
                    {h.text}
                  </div>
                );
              })}
            </div>
          )}
          {filterMl && hasOmnibusSelected && (
            <div className="mt-2 text-[10px] text-amber-400 flex items-start gap-1.5">
              <span aria-hidden>⚠</span>
              <span>Les recettes omnibus exigent ≥ 2200 bougies pour un entraînement fiable.</span>
            </div>
          )}
        </div>

        <div>
          <div className="text-xs text-dim mb-2">
            Symboles
            {availableSymbols !== FALLBACK_SYMBOLS && (
              <span className="text-[10px] text-emerald-400 ml-1">(depuis config)</span>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {availableSymbols.map((sym) => {
              const active = selectedSymbols.includes(sym);
              return (
                <button
                  key={sym}
                  onClick={() => toggle(selectedSymbols, sym, setSelectedSymbols)}
                  className={cn(
                    'px-3 py-1.5 rounded-lg text-xs font-mono border transition-all',
                    active
                      ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40'
                      : 'bg-card-hover text-muted border-border hover:border-border-hi',
                  )}
                >
                  {sym}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <label className="text-xs text-dim block mb-1.5">Nombre de bougies</label>
          <input
            aria-label="Nombre de bougies"
            type="number"
            min={0}
            max={50000}
            step={100}
            value={limitPerTf}
            onChange={(e) => setLimitPerTf(Math.max(0, Math.min(50000, Number(e.target.value) || 0)))}
            className="w-full max-w-xs px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
          />
          <div className="flex flex-wrap gap-1 mt-1">
            {[500, 2000, 5000, 8000, 50000].map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => setLimitPerTf(n)}
                className={cn(
                  'px-2 py-0.5 rounded text-[10px] border transition-colors',
                  limitPerTf === n
                    ? 'bg-cyan-500/20 text-cyan-800 dark:text-cyan-300 border-cyan-500/40'
                    : 'bg-surface border-border text-muted hover:text-foreground hover:border-border-hi',
                )}
              >
                {n >= 1000 ? `${n / 1000}k` : n}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setLimitPerTf(0)}
              className={cn(
                'px-2 py-0.5 rounded text-[10px] border',
                limitPerTf === 0
                  ? 'bg-cyan-500/20 text-cyan-800 dark:text-cyan-300 border-cyan-500/40'
                  : 'bg-surface border-border text-muted',
              )}
            >
              Auto
            </button>
          </div>
          <p className="text-[10px] text-dim mt-1">0 / Auto = profondeur max du cache par TF.</p>
        </div>

        <div>
          <div className="text-xs text-dim mb-2">Presets</div>
          <div className="flex flex-wrap gap-2">
            {(Object.keys(PRESETS) as PresetKey[]).map((key) => {
              const p = PRESETS[key];
              const active = activePreset === key;
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => applyPreset(key)}
                  className={cn(
                    'px-3 py-1.5 rounded-lg text-xs border transition-all text-left',
                    active
                      ? 'bg-cyan-500/15 text-cyan-400 border-cyan-500/40'
                      : 'bg-card-hover text-muted border-border hover:border-border-hi',
                  )}
                  title={p.description}
                >
                  <div className="font-semibold">{p.label}</div>
                  <div className="text-[10px] text-dim">{p.description}</div>
                </button>
              );
            })}
            {activePreset === null && (
              <div className="px-3 py-1.5 rounded-lg text-xs border bg-amber-500/10 text-amber-400 border-amber-500/30">
                <div className="font-semibold">Custom</div>
                <div className="text-[10px] text-dim">Configuration manuelle</div>
              </div>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <div>
            <label className="text-xs text-dim block mb-1.5">Méthode</label>
            <select
              aria-label="Méthode"
              value={method}
              onChange={(e) => setMethod(e.target.value as (typeof METHODS)[number])}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm"
            >
              {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Nombre d&apos;essais (trials)</label>
            <input
              aria-label="Nombre d'essais"
              type="number"
              min={5}
              max={500}
              value={nTrials}
              onChange={(e) => { setNTrials(Math.max(1, Number(e.target.value) || 1)); markCustom(); }}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
            />
            {/* LAB-04 : le moteur reproportionne le budget à
                `trials_per_param x n_params`, plafonné. Annoncer le nombre
                demandé laissait l'opérateur découvrir 400 essais après en
                avoir demandé 60. */}
            {budget.data && budget.data.max > 0 && (
              <p className={cn('text-[10px] mt-1',
                budget.data.max > nTrials ? 'text-amber-400' : 'text-dim')}>
                {budget.data.min === budget.data.max
                  ? `${budget.data.max} essais réels`
                  : `${budget.data.min} à ${budget.data.max} essais réels selon la stratégie`}
                {' · '}{budget.data.total} au total
                {budget.data.max > nTrials
                  && ' — proportionné au nombre de paramètres'}
              </p>
            )}
          </div>
          <div>
            <label className="text-xs text-dim block mb-1.5">Workers parallèles</label>
            <input
              aria-label="Workers parallèles"
              type="number"
              min={1}
              max={16}
              value={nJobs}
              onChange={(e) => { setNJobs(Math.max(1, Number(e.target.value) || 1)); markCustom(); }}
              className="w-full px-3 py-2 bg-card-hover border border-border rounded-md text-sm font-mono"
            />
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm cursor-pointer h-10">
              <input
                type="checkbox"
                checked={autoApply}
                onChange={(e) => setAutoApply(e.target.checked)}
                className="rounded"
              />
              Auto-apply
            </label>
          </div>
          <div className="flex items-end">
            <label
              className="flex items-center gap-2 text-sm cursor-pointer h-10"
              title="Gel des paramètres à faible impact pendant la recherche (dépistage dans le budget d'essais, espaces larges uniquement)"
            >
              <input
                type="checkbox"
                checked={paramSearchOptim}
                onChange={(e) => setParamSearchOptim(e.target.checked)}
                className="rounded"
              />
              Param Search Optim
            </label>
          </div>
        </div>

        <div className="space-y-3 pt-2 border-t border-border">
          <div className="text-[10px] uppercase tracking-wider text-dim font-semibold">
            Options avancées
          </div>
          <div className="flex items-center gap-2 text-xs">
            <label className="cursor-pointer flex items-center gap-2">
              Arrêt anticipé (patience)
              <input
                aria-label="Arrêt anticipé (patience)"
                type="number"
                min={0}
                max={50}
                value={earlyStopping}
                onChange={(e) => setEarlyStopping(Math.max(0, Math.min(50, Number(e.target.value) || 0)))}
                className="w-20 px-2 py-1 bg-card-hover border border-border rounded-md text-xs font-mono"
              />
              <span className="text-dim">0 = désactivé</span>
            </label>
          </div>
          {(!filterMl || hasMlSelected) && (
            <div className="flex items-center gap-2 text-xs">
              <input
                id="opt-ml-tune"
                type="checkbox"
                checked={mlTuneHp}
                onChange={(e) => setMlTuneHp(e.target.checked)}
                disabled={!hasMlSelected}
                className="rounded"
              />
              <label htmlFor="opt-ml-tune" className={cn('cursor-pointer', !hasMlSelected && 'opacity-50')}>
                Régler aussi les hyperparamètres ML
                <span className="text-dim ml-1">(plus lent, stratégies ML uniquement)</span>
              </label>
            </div>
          )}
        </div>

        {selectedStrategies.length > 0 && selectedTfs.length > 0 && selectedSymbols.length > 0 && (
          <div className="rounded-lg border border-border bg-card-hover/50 p-3 space-y-2">
            <div className="text-[11px] text-dim uppercase tracking-wider font-semibold">
              Preview run — {selectedStrategies.length}×{selectedTfs.length}×{selectedSymbols.length}
              {' = '}
              <span className="text-foreground font-mono">
                {selectedStrategies.length * selectedTfs.length * selectedSymbols.length}
              </span>
              {' '}slots
            </div>
            <div className="overflow-x-auto max-h-40">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-dim border-b border-border">
                    <th className="text-left p-1.5 font-medium">Stratégie</th>
                    {selectedTfs.map((tf) => (
                      <th key={tf} className="p-1.5 font-mono font-medium text-center">{tf}</th>
                    ))}
                    <th className="p-1.5 font-medium text-right">Combos</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedStrategies.map((s) => {
                    const info = spaces?.[s];
                    const recTfs: string[] = info?.recommended_tfs ?? info?.timeframes ?? [];
                    return (
                      <tr key={s} className="border-b border-border/40">
                        <td className="p-1.5 font-mono font-semibold">{s}</td>
                        {selectedTfs.map((tf) => {
                          const rec = recTfs.length === 0 || recTfs.includes(tf);
                          return (
                            <td key={tf} className="p-1.5 text-center">
                              <span
                                className={cn(
                                  'inline-block w-2 h-2 rounded-full',
                                  rec ? 'bg-emerald-400' : 'bg-amber-400/70',
                                )}
                                title={rec ? 'TF recommandé' : 'TF hors liste recommandée'}
                              />
                            </td>
                          );
                        })}
                        <td className="p-1.5 text-right font-mono text-muted">
                          {info?.n_combos ?? '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="text-[10px] text-dim">
              Point vert = TF recommandé · ambre = hors liste.
              Symboles : {selectedSymbols.join(', ')} · méthode {method} · {nTrials} trials · {nJobs} worker(s)
            </p>
          </div>
        )}

        {launchFeedback?.kind === 'fetching' && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300 flex items-center gap-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            Récupération des bougies en cours…
          </div>
        )}
        {launchFeedback?.kind === 'ok' && (
          <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300 space-y-1">
            <div className="flex items-center gap-2 font-semibold">
              <CheckCircle2 className="w-3.5 h-3.5" />
              ✓ {launchFeedback.nJobs} job(s) lancé(s)
            </div>
            {launchFeedback.receivedBars && Object.keys(launchFeedback.receivedBars).length > 0 && (
              <div className="font-mono text-[0.65rem] opacity-80">
                Bougies reçues :{' '}
                {Object.entries(launchFeedback.receivedBars).map(([tf, n]) => `${tf}=${n}`).join(' · ')}
              </div>
            )}
            {Array.isArray(launchFeedback.skipped) && launchFeedback.skipped.length > 0 && (
              <div className="font-mono text-[0.65rem] text-amber-300">
                ⚠ {launchFeedback.skipped.length} combinaison(s) ignorée(s) (espace vide ou stratégie sans params).
              </div>
            )}
          </div>
        )}

        <Button onClick={handleStart} disabled={startOptimize.isPending} variant="primary">
          {startOptimize.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Play className="w-4 h-4" fill="currentColor" />
          )}
          {filterMl ? "⬡ Lancer l'optimisation ML" : "Lancer l'optimisation"}
        </Button>

        <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/5 px-3 py-2 text-xs text-cyan-200 flex items-start gap-2">
          <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <div>
            Les paramètres globaux ne sont jamais modifiés par l&apos;optimiseur :{' '}
            <code className="font-mono">score_threshold</code>,{' '}
            <code className="font-mono">risk_per_trade</code>,{' '}
            <code className="font-mono">capital</code>,{' '}
            <code className="font-mono">timeframe</code>,{' '}
            <code className="font-mono">paper_mode</code>.
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
