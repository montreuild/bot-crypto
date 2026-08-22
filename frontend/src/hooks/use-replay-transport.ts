'use client';

/**
 * Transport du rejeu : position, lecture, raccourcis clavier (ARCH-02).
 *
 * Extrait de `smart-replay-view.tsx`. Toute commande met la lecture en pause —
 * on ne se bat pas contre le minuteur en naviguant à la main.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

export const SPEEDS = [
  { label: '1x', ms: 1000 },
  { label: '2x', ms: 500 },
  { label: '5x', ms: 200 },
  { label: '10x', ms: 100 },
] as const;

const SAUT_SHIFT = 10;

export function useReplayTransport(nBars: number) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speedIdx, setSpeedIdx] = useState(1);   // 2x

  // Un changement de symbole ou de TF change `nBars` : la position doit rester
  // dans le nouvel intervalle plutôt que pointer au-delà de la série.
  useEffect(() => {
    setCurrentIndex((idx) => (nBars > 0 ? Math.max(0, Math.min(idx, nBars - 1)) : 0));
  }, [nBars]);

  useEffect(() => {
    if (!isPlaying) return;
    const id = setInterval(() => {
      setCurrentIndex((idx) => {
        const next = idx + 1;
        if (next >= nBars - 1) {
          setIsPlaying(false);
          return nBars - 1;
        }
        return next;
      });
    }, SPEEDS[speedIdx].ms);
    return () => clearInterval(id);
  }, [isPlaying, speedIdx, nBars]);

  const goToStart = useCallback(() => { setIsPlaying(false); setCurrentIndex(0); }, []);
  const goToEnd = useCallback(() => {
    setIsPlaying(false); setCurrentIndex(Math.max(0, nBars - 1));
  }, [nBars]);
  const jump = useCallback((delta: number) => {
    setIsPlaying(false);
    setCurrentIndex((i) => Math.max(0, Math.min(nBars - 1, i + delta)));
  }, [nBars]);
  /** Positionnement direct (curseur) — met la lecture en pause comme le reste. */
  const seekTo = useCallback((idx: number) => {
    setIsPlaying(false);
    setCurrentIndex(Math.max(0, Math.min(nBars - 1, idx)));
  }, [nBars]);
  const stepBack = useCallback(() => jump(-1), [jump]);
  const stepForward = useCallback(() => jump(1), [jump]);
  const togglePlay = useCallback(() => {
    // Relancer depuis la fin repart du début plutôt que de ne rien faire.
    setCurrentIndex((i) => (i >= nBars - 1 ? 0 : i));
    setIsPlaying((p) => !p);
  }, [nBars]);

  // Espace, ← →, Shift+← → (±10), Home, End — hérités de la page Jinja2.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      const actions: Record<string, () => void> = {
        Space: togglePlay,
        ArrowLeft: () => jump(e.shiftKey ? -SAUT_SHIFT : -1),
        ArrowRight: () => jump(e.shiftKey ? SAUT_SHIFT : 1),
        Home: goToStart,
        End: goToEnd,
      };
      const action = actions[e.code];
      if (!action) return;
      e.preventDefault();
      action();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [togglePlay, jump, goToStart, goToEnd]);

  return useMemo(() => ({
    currentIndex, isPlaying, speedIdx, setSpeedIdx,
    seekTo, goToStart, goToEnd, stepBack, stepForward, jump, togglePlay,
  }), [currentIndex, isPlaying, speedIdx,
       seekTo, goToStart, goToEnd, stepBack, stepForward, jump, togglePlay]);
}
