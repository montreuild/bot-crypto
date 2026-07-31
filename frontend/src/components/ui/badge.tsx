'use client';

import { cn } from '@/lib/utils';
import { HTMLAttributes } from 'react';

type Variant = 'default' | 'muted' | 'success' | 'danger' | 'warning' | 'info' | 'purple';

const variants: Record<Variant, string> = {
  default: 'bg-card-hover text-foreground border-border',
  // Badge neutre atténué — utilisé pour les états passifs (« Gelé manuellement »,
  // « Expert requis », compteurs de features). Introduit en S4/S6/S9 sans avoir
  // été déclaré ici : `variants[variant]` renvoyait `undefined` et le badge
  // s'affichait sans aucun style.
  muted: 'bg-card-hover/60 text-dim border-border',
  success: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
  danger: 'bg-red-500/10 text-red-400 border-red-500/30',
  warning: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
  info: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30',
  purple: 'bg-purple-500/10 text-purple-400 border-purple-500/30',
};

export function Badge({
  variant = 'default',
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border',
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}
