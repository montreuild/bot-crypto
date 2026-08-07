/**
 * RPL-005 — Raccourcis clavier pour le replay interactif.
 *
 * Extrait du template Jinja2 `replay.html:734-749`.
 * - Espace → play/pause
 * - ← / → → ±1 bougie (Shift = ±10)
 * - Home / End → début / fin
 * - 1 / 2 / 5 / 0 → vitesses 1× / 2× / 5× / MAX
 *
 * Désactivé si le focus est dans un input/textarea/select.
 */

import { useEffect } from 'react';
import type { ReplaySpeed } from './use-replay-engine';

interface UseReplayKeyboardParams {
  onTogglePlay: () => void;
  onStep: (delta: number) => void;
  onSeek: (pos: number) => void;
  onSetSpeed: (s: ReplaySpeed) => void;
  total: number;
  /** Position courante (pour Home/End relatif). Inutilisé — Home=0, End=total-1. */
  enabled?: boolean;
}

export function useReplayKeyboard({
  onTogglePlay,
  onStep,
  onSeek,
  onSetSpeed,
  total,
  enabled = true,
}: UseReplayKeyboardParams) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      // Ne pas intercepter si l'utilisateur est dans un contenteditable.
      if ((e.target as HTMLElement)?.isContentEditable) return;

      if (e.code === 'Space') {
        e.preventDefault();
        onTogglePlay();
      } else if (e.code === 'ArrowLeft') {
        e.preventDefault();
        onStep(e.shiftKey ? -10 : -1);
      } else if (e.code === 'ArrowRight') {
        e.preventDefault();
        onStep(e.shiftKey ? 10 : 1);
      } else if (e.code === 'Home') {
        e.preventDefault();
        onSeek(0);
      } else if (e.code === 'End') {
        e.preventDefault();
        onSeek(total - 1);
      } else if (e.key === '1') {
        onSetSpeed(1);
      } else if (e.key === '2') {
        onSetSpeed(2);
      } else if (e.key === '5') {
        onSetSpeed(5);
      } else if (e.key === '0') {
        onSetSpeed('MAX');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enabled, onTogglePlay, onStep, onSeek, onSetSpeed, total]);
}
