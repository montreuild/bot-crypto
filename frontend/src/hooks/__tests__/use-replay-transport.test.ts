/**
 * ARCH-02 — le transport du rejeu, testable sans monter la vue.
 *
 * Deux invariants tenaient dans 90 lignes noyées au milieu de 744 : toute
 * commande met la lecture en pause, et la position reste dans la série même
 * quand celle-ci change de longueur sous elle (changement de symbole ou de TF).
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SPEEDS, useReplayTransport } from '@/hooks/use-replay-transport';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('useReplayTransport — navigation', () => {
  it('démarre au début, à l’arrêt, en 2x', () => {
    const { result } = renderHook(() => useReplayTransport(100));
    expect(result.current.currentIndex).toBe(0);
    expect(result.current.isPlaying).toBe(false);
    expect(SPEEDS[result.current.speedIdx].label).toBe('2x');
  });

  it('borne les déplacements aux extrémités de la série', () => {
    const { result } = renderHook(() => useReplayTransport(10));
    act(() => result.current.stepBack());
    expect(result.current.currentIndex).toBe(0);
    act(() => result.current.goToEnd());
    expect(result.current.currentIndex).toBe(9);
    act(() => result.current.stepForward());
    expect(result.current.currentIndex).toBe(9);
    act(() => result.current.jump(999));
    expect(result.current.currentIndex).toBe(9);
    act(() => result.current.jump(-999));
    expect(result.current.currentIndex).toBe(0);
  });

  it('seekTo borne aussi, et met en pause', () => {
    const { result } = renderHook(() => useReplayTransport(10));
    act(() => result.current.togglePlay());
    expect(result.current.isPlaying).toBe(true);
    act(() => result.current.seekTo(50));
    expect(result.current.currentIndex).toBe(9);
    expect(result.current.isPlaying).toBe(false);
  });

  it('une série vide ne produit pas d’index négatif', () => {
    const { result } = renderHook(() => useReplayTransport(0));
    act(() => result.current.goToEnd());
    expect(result.current.currentIndex).toBe(0);
  });
});

describe('useReplayTransport — lecture', () => {
  it('avance d’une barre par intervalle et s’arrête à la fin', () => {
    const { result } = renderHook(() => useReplayTransport(4));
    act(() => result.current.togglePlay());
    act(() => { vi.advanceTimersByTime(SPEEDS[1].ms); });
    expect(result.current.currentIndex).toBe(1);
    act(() => { vi.advanceTimersByTime(SPEEDS[1].ms * 5); });
    expect(result.current.currentIndex).toBe(3);
    expect(result.current.isPlaying).toBe(false);
  });

  it('relancer depuis la fin repart du début', () => {
    const { result } = renderHook(() => useReplayTransport(4));
    act(() => result.current.goToEnd());
    expect(result.current.currentIndex).toBe(3);
    act(() => result.current.togglePlay());
    expect(result.current.currentIndex).toBe(0);
    expect(result.current.isPlaying).toBe(true);
  });

  it('la vitesse choisie pilote l’intervalle', () => {
    const { result } = renderHook(() => useReplayTransport(100));
    act(() => result.current.setSpeedIdx(3));         // 10x = 100 ms
    act(() => result.current.togglePlay());
    act(() => { vi.advanceTimersByTime(300); });
    expect(result.current.currentIndex).toBe(3);
  });

  it('toute commande met la lecture en pause', () => {
    for (const cmd of ['stepForward', 'stepBack', 'goToStart', 'goToEnd'] as const) {
      const { result } = renderHook(() => useReplayTransport(20));
      act(() => result.current.togglePlay());
      expect(result.current.isPlaying).toBe(true);
      act(() => result.current[cmd]());
      expect(result.current.isPlaying, cmd).toBe(false);
    }
  });
});

describe('useReplayTransport — la série change sous la position', () => {
  it('ramène l’index dans la nouvelle série, plus courte', () => {
    const { result, rerender } = renderHook(({ n }) => useReplayTransport(n),
      { initialProps: { n: 100 } });
    act(() => result.current.goToEnd());
    expect(result.current.currentIndex).toBe(99);
    rerender({ n: 10 });
    expect(result.current.currentIndex).toBe(9);
  });

  it('remet à zéro quand la série disparaît', () => {
    const { result, rerender } = renderHook(({ n }) => useReplayTransport(n),
      { initialProps: { n: 50 } });
    act(() => result.current.seekTo(30));
    rerender({ n: 0 });
    expect(result.current.currentIndex).toBe(0);
  });

  it('ne déplace pas une position déjà valide', () => {
    const { result, rerender } = renderHook(({ n }) => useReplayTransport(n),
      { initialProps: { n: 50 } });
    act(() => result.current.seekTo(20));
    rerender({ n: 80 });
    expect(result.current.currentIndex).toBe(20);
  });
});

describe('useReplayTransport — raccourcis clavier', () => {
  const touche = (code: string, init: KeyboardEventInit = {}) =>
    act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { code, ...init })); });

  it('Espace bascule la lecture, flèches et Home/End déplacent', () => {
    const { result } = renderHook(() => useReplayTransport(100));
    touche('Space');
    expect(result.current.isPlaying).toBe(true);
    touche('ArrowRight');
    expect(result.current.currentIndex).toBe(1);
    expect(result.current.isPlaying).toBe(false);
    touche('ArrowRight', { shiftKey: true });
    expect(result.current.currentIndex).toBe(11);
    touche('ArrowLeft', { shiftKey: true });
    expect(result.current.currentIndex).toBe(1);
    touche('End');
    expect(result.current.currentIndex).toBe(99);
    touche('Home');
    expect(result.current.currentIndex).toBe(0);
  });

  it('ignore les touches quand la saisie est dans un champ', () => {
    const { result } = renderHook(() => useReplayTransport(100));
    const input = document.createElement('input');
    document.body.appendChild(input);
    act(() => {
      input.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', bubbles: true }));
    });
    expect(result.current.isPlaying).toBe(false);
    input.remove();
  });

  it('libère l’écouteur au démontage', () => {
    const { result, unmount } = renderHook(() => useReplayTransport(100));
    unmount();
    touche('ArrowRight');
    expect(result.current.currentIndex).toBe(0);
  });
});
