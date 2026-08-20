'use client';

import { useState, useEffect, Suspense } from 'react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useSearchParams, useRouter } from 'next/navigation';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { usePresets, useSetExpertMode } from '@/hooks/use-api';
import { toast } from 'sonner';
import {
  FlaskConical, Sparkles, Brain, Repeat, GitCompare, Rocket, Archive, Layers,
} from 'lucide-react';

const tabLoading = () => <div className="p-8 text-center text-sm text-muted">Chargement…</div>;

const BacktestView = dynamic(
  () => import('@/components/views/backtest-view').then((m) => m.BacktestView),
  { loading: tabLoading },
);
const OptimizerView = dynamic(
  () => import('@/components/views/optimizer-view').then((m) => m.OptimizerView),
  { loading: tabLoading },
);
const MLView = dynamic(
  () => import('@/components/views/ml-view').then((m) => m.MLView),
  { loading: tabLoading },
);
const ReplayView = dynamic(
  () => import('@/components/views/replay-view').then((m) => m.ReplayView),
  { loading: tabLoading },
);
const MultiTfBatchView = dynamic(
  () => import('@/components/views/multi-tf-batch-view').then((m) => m.MultiTfBatchView),
  { loading: tabLoading },
);
const CompareView = dynamic(
  () => import('@/components/views/compare-view').then((m) => m.CompareView),
  { loading: tabLoading },
);

const TABS = ['backtest', 'optimizer', 'ml', 'replay', 'batch', 'compare'] as const;

export default function LabPage() {
  return (
    <Suspense fallback={<div className="p-6 text-muted">Chargement…</div>}>
      <LabContent />
    </Suspense>
  );
}

function LabContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const requestedTab = searchParams.get('tab');
  const tabValid = TABS.includes(requestedTab as (typeof TABS)[number]);
  const initialTab = tabValid ? requestedTab! : 'backtest';
  const intent = searchParams.get('intent');
  const [tab, setTab] = useState(initialTab);

  useEffect(() => {
    if (requestedTab && !tabValid) {
      const q = new URLSearchParams(searchParams.toString());
      q.set('tab', 'backtest');
      router.replace(`/lab?${q.toString()}`, { scroll: false });
    }
  }, [requestedTab, tabValid, router, searchParams]);

  const presetsQuery = usePresets();
  const setExpertModeMutation = useSetExpertMode();
  const [localExpert, setLocalExpert] = useState(false);
  useEffect(() => {
    setLocalExpert(localStorage.getItem('expert_mode') === 'true');
  }, []);
  const expertMode = presetsQuery.data ? !!presetsQuery.data.expert_mode : localExpert;

  const handleExpertToggle = async (checked: boolean) => {
    setLocalExpert(checked);
    localStorage.setItem('expert_mode', String(checked));
    try {
      await setExpertModeMutation.mutateAsync(checked);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`Mode expert non enregistré : ${msg}`);
    }
  };

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-end justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Laboratoire</h1>
          <p className="text-sm text-muted mt-1">
            Analyse, optimise et entraîne tes stratégies · workflow guidé en verdict clair
          </p>
        </div>
        <div className="flex items-center gap-3">
          {intent === 'create' && (
            <Badge variant="info" className="text-xs">
              <Rocket className="w-3 h-3" />
              Nouveau bot
            </Badge>
          )}
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <Switch
              checked={expertMode}
              onCheckedChange={handleExpertToggle}
              aria-label="Mode expert"
            />
            <span className="text-muted">Mode expert</span>
          </label>
        </div>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="grid grid-cols-6 w-full max-w-3xl">
          <TabsTrigger value="backtest">
            <FlaskConical className="w-3.5 h-3.5 mr-1.5" />
            Backtest
          </TabsTrigger>
          <TabsTrigger value="optimizer">
            <Sparkles className="w-3.5 h-3.5 mr-1.5" />
            Optimizer
          </TabsTrigger>
          <TabsTrigger value="ml">
            <Brain className="w-3.5 h-3.5 mr-1.5" />
            ML Train
          </TabsTrigger>
          <TabsTrigger value="replay">
            <Repeat className="w-3.5 h-3.5 mr-1.5" />
            Replay
          </TabsTrigger>
          <TabsTrigger value="batch">
            <Layers className="w-3.5 h-3.5 mr-1.5" />
            Multi-TF
          </TabsTrigger>
          <TabsTrigger value="compare">
            <GitCompare className="w-3.5 h-3.5 mr-1.5" />
            Compare
          </TabsTrigger>
        </TabsList>

        <TabsContent value="backtest">
          <BacktestView expertMode={expertMode} />
        </TabsContent>
        <TabsContent value="optimizer">
          <OptimizerView />
        </TabsContent>
        <TabsContent value="ml">
          <MLTab />
        </TabsContent>
        <TabsContent value="replay">
          <ReplayView />
        </TabsContent>
        <TabsContent value="batch">
          <MultiTfBatchView />
        </TabsContent>
        <TabsContent value="compare">
          <CompareView />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function MLTab() {
  return (
    <div className="space-y-4">
      <MLView />
      <Card>
        <CardContent className="p-4 flex items-center justify-between gap-4 flex-wrap">
          <p className="text-sm text-muted">
            Le registre versionné (gate de promotion, pin, sweep) reste une page dédiée.
          </p>
          <Link
            href="/models"
            className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md border border-border text-sm text-muted hover:text-foreground hover:bg-card-hover transition-colors"
          >
            <Archive className="w-3.5 h-3.5" />
            Registre modèles
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
