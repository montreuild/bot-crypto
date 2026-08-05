import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ── Formatage ───────────────────────────────────────────────────────────────

export function formatUSD(value: number, opts: { decimals?: number; sign?: boolean } = {}): string {
  const { decimals = 2, sign = false } = opts;
  const formatter = new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD',
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
    signDisplay: sign ? 'always' : 'auto',
  });
  return formatter.format(value);
}

export function formatPct(value: number, decimals = 2, sign = true): string {
  const formatted = value.toFixed(decimals);
  return sign && value > 0 ? `+${formatted}%` : `${formatted}%`;
}

export function formatNumber(value: number, decimals = 2): string {
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  }).format(value);
}

export function formatCompact(value: number): string {
  return new Intl.NumberFormat('en-US', {
    notation: 'compact', maximumFractionDigits: 1,
  }).format(value);
}

export function formatTime(iso: string | number | null | undefined): string {
  if (!iso) return '—';
  const d = typeof iso === 'number' ? new Date(iso * 1000) : new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function formatDateTime(iso: string | number | null | undefined): string {
  if (!iso) return '—';
  const d = typeof iso === 'number' ? new Date(iso * 1000) : new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

export function timeAgo(iso: string | number | null | undefined): string {
  if (!iso) return 'jamais';
  const d = typeof iso === 'number' ? new Date(iso * 1000) : new Date(iso);
  if (isNaN(d.getTime())) return '—';
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}min`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}j`;
}

// ── Couleurs PnL ────────────────────────────────────────────────────────────

export function pnlColor(value: number): string {
  if (value > 0) return 'text-profit';
  if (value < 0) return 'text-loss';
  return 'text-neutral-pnl';
}

export function pnlBgColor(value: number): string {
  if (value > 0) return 'bg-profit-dim';
  if (value < 0) return 'bg-loss-dim';
  return '';
}

// ── Slot keys ───────────────────────────────────────────────────────────────

export function parseSlotKey(slotKey: string): { strategy: string; tf: string; symbol?: string } {
  const parts = slotKey.split('::');
  if (parts.length >= 3) return { strategy: parts[0], tf: parts[1], symbol: parts.slice(2).join('::') };
  if (parts.length === 2) return { strategy: parts[0], tf: parts[1] };
  return { strategy: slotKey, tf: '' };
}

// ── Cycle de vie ────────────────────────────────────────────────────────────

export const LIFECYCLE_COLORS: Record<string, { bg: string; text: string; label: string; icon: string }> = {
  candidat: { bg: 'bg-amber-500/10', text: 'text-amber-400', label: 'Candidat', icon: '○' },
  essai: { bg: 'bg-cyan-500/10', text: 'text-cyan-400', label: 'Essai', icon: '◐' },
  actif: { bg: 'bg-emerald-500/10', text: 'text-emerald-400', label: 'Actif', icon: '●' },
  retire: { bg: 'bg-red-500/10', text: 'text-red-400', label: 'Retiré', icon: '✕' },
};

export function lifecycleStyle(state: string) {
  return LIFECYCLE_COLORS[state] || LIFECYCLE_COLORS.candidat;
}

// ── Theme (dark/light) ──────────────────────────────────────────────────────

export function getStoredTheme(): 'dark' | 'light' {
  if (typeof window === 'undefined') return 'dark';
  const stored = localStorage.getItem('theme');
  if (stored === 'light' || stored === 'dark') return stored;
  if (window.matchMedia('(prefers-color-scheme: light)').matches) return 'light';
  return 'dark';
}

export function setStoredTheme(theme: 'dark' | 'light') {
  if (typeof window === 'undefined') return;
  localStorage.setItem('theme', theme);
  const root = document.documentElement;
  // Remplacer explicitement les classes (toggle seul échouait si React
  // réappliquait `dark` sur <html> via le layout).
  root.classList.remove('light', 'dark');
  root.classList.add(theme === 'light' ? 'light' : 'dark');
}
