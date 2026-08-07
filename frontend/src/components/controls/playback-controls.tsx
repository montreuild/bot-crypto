/**
 * RPL-001 — Barre de contrôles playback (transport + vitesses + progress bar).
 *
 * Inspired by `smart-replay-view.tsx` transport bar (l. 514-569), étendu avec
 * les 7 vitesses du template Jinja2 `replay.html:203-232` (0.5× à MAX).
 */

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  SkipBack, SkipForward, ChevronLeft, ChevronRight,
  Play, Pause,
} from 'lucide-react';
import type { ReplaySpeed } from '@/hooks/use-replay-engine';

const SPEEDS: ReplaySpeed[] = [0.5, 1, 2, 5, 10, 20, 'MAX'];

interface Props {
  position: number;
  total: number;
  isPlaying: boolean;
  speed: ReplaySpeed;
  onPlayPause: () => void;
  onStep: (delta: number) => void;
  onSeek: (pos: number) => void;
  onSpeedChange: (s: ReplaySpeed) => void;
}

export function PlaybackControls({
  position,
  total,
  isPlaying,
  speed,
  onPlayPause,
  onStep,
  onSeek,
  onSpeedChange,
}: Props) {
  const pct = total > 0 ? (position / (total - 1)) * 100 : 0;
  return (
    <div className="flex flex-wrap items-center gap-4 p-3 bg-card border border-border rounded-md">
      {/* Transport */}
      <div className="flex items-center gap-1">
        <Button
          size="icon"
          variant="ghost"
          onClick={() => onSeek(0)}
          title="Début (Home)"
          aria-label="Début"
          disabled={total === 0}
        >
          <SkipBack className="h-4 w-4" />
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onStep(-10)}
          title="−10 barres (Shift+←)"
          aria-label="Reculer 10 barres"
          disabled={total === 0}
        >
          −10
        </Button>
        <Button
          size="icon"
          variant="ghost"
          onClick={() => onStep(-1)}
          title="Reculer 1 barre (←)"
          aria-label="Reculer 1 barre"
          disabled={total === 0}
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <Button
          size="icon"
          variant="primary"
          onClick={onPlayPause}
          title="Play/Pause (Espace)"
          aria-label="Play/Pause"
          disabled={total === 0}
        >
          {isPlaying ? (
            <Pause className="h-4 w-4" fill="currentColor" />
          ) : (
            <Play className="h-4 w-4" fill="currentColor" />
          )}
        </Button>
        <Button
          size="icon"
          variant="ghost"
          onClick={() => onStep(1)}
          title="Avancer 1 barre (→)"
          aria-label="Avancer 1 barre"
          disabled={total === 0}
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onStep(10)}
          title="+10 barres (Shift+→)"
          aria-label="Avancer 10 barres"
          disabled={total === 0}
        >
          +10
        </Button>
        <Button
          size="icon"
          variant="ghost"
          onClick={() => onSeek(total - 1)}
          title="Fin (End)"
          aria-label="Fin"
          disabled={total === 0}
        >
          <SkipForward className="h-4 w-4" />
        </Button>
      </div>

      {/* Progress bar scrubbable */}
      <div className="flex-1 min-w-[200px] flex items-center gap-2">
        <input
          type="range"
          min={0}
          max={Math.max(0, total - 1)}
          value={position}
          onChange={(e) => onSeek(Number(e.target.value))}
          className="w-full accent-cyan-400"
          aria-label="Position dans le replay"
          disabled={total === 0}
        />
      </div>

      {/* Compteur position */}
      <div className="text-xs font-mono text-muted-foreground whitespace-nowrap">
        {total > 0 ? `${position + 1} / ${total}` : '— / —'}
        <span className="ml-2 text-dim">({pct.toFixed(0)}%)</span>
      </div>

      {/* Vitesses */}
      <div className="flex items-center gap-1">
        {SPEEDS.map((s) => (
          <Button
            key={String(s)}
            size="sm"
            variant={speed === s ? 'primary' : 'ghost'}
            onClick={() => onSpeedChange(s)}
            className="h-7 px-2 text-xs font-mono"
            title={`Vitesse ${s}×${s === 'MAX' ? ' (batch 100 bougies/frame)' : ''}`}
            disabled={total === 0}
          >
            {s === 'MAX' ? 'MAX' : `${s}×`}
          </Button>
        ))}
      </div>

      {/* Indicateur lecture */}
      {isPlaying && (
        <Badge variant="info" className="animate-pulse">
          <Play className="h-3 w-3" fill="currentColor" />
          Lecture {speed === 'MAX' ? 'MAX' : `${speed}×`}
        </Badge>
      )}
    </div>
  );
}
