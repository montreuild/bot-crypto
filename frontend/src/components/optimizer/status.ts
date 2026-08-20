export const METHODS = ['grid', 'random', 'bayesian'] as const;

export const FALLBACK_SYMBOLS = ['BTC/USDC', 'ETH/USDC', 'SOL/USDC', 'BNB/USDC', 'XRP/USDC'];

export const PRESETS = {
  fast: { nTrials: 20, nJobs: 1, earlyStopping: 10, mlTuneHp: false, label: 'Rapide', description: '20 trials, 1 worker — ~2 min' },
  balanced: { nTrials: 60, nJobs: 2, earlyStopping: 15, mlTuneHp: false, label: 'Équilibré', description: '60 trials, 2 workers — ~10 min' },
  deep: { nTrials: 150, nJobs: 2, earlyStopping: 0, mlTuneHp: true, label: 'Approfondi', description: '150 trials, 2 workers, ML HP — ~45 min' },
} as const;
export type PresetKey = keyof typeof PRESETS;

export const STATUS_VARIANT: Record<string, 'success' | 'danger' | 'warning' | 'info' | 'default'> = {
  pending: 'warning',
  running: 'info',
  done: 'success',
  error: 'danger',
  cancelled: 'default',
  queued: 'warning',
  skipped: 'default',
};

export const STATUS_LABEL: Record<string, string> = {
  pending: 'En attente',
  running: 'En cours',
  done: 'Terminé',
  error: 'Erreur',
  cancelled: 'Annulé',
  queued: 'En file',
  skipped: 'Ignoré',
};
