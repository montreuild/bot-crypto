'use client';

import { cn } from '@/lib/utils';
import { ButtonHTMLAttributes, forwardRef } from 'react';

type Variant = 'default' | 'primary' | 'success' | 'danger' | 'ghost' | 'outline';
type Size = 'sm' | 'md' | 'lg' | 'icon';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const variantClasses: Record<Variant, string> = {
  default: 'bg-card text-foreground border border-border hover:bg-card-hover',
  // S10-a11y — `text-white` sur `bg-primary-500` (#06b6d4) ne donnait que
  // 2.43:1, sous le seuil AA de 4.5:1. C'était la source des 22 violations
  // `color-contrast` relevées par axe-core : le bouton principal est présent
  // sur presque toutes les pages. Le cyan est trop clair pour du texte blanc.
  // Texte sombre dessus : 7.97:1 au repos, 5.25:1 au survol — et la couleur de
  // marque est préservée.
  // Clair : texte slate-900 sur cyan-500 (≥ 7:1). Sombre : même texte sombre
  // (le fond page n'est plus utilisable — en light c'est #f8fafc, 2.3:1).
  primary: 'bg-primary-500 text-slate-900 hover:bg-primary-400 hover:text-slate-900',
  success: 'bg-emerald-500/10 text-emerald-800 dark:text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/20',
  danger: 'bg-red-500/10 text-red-700 dark:text-red-400 border border-red-500/30 hover:bg-red-500/20',
  ghost: 'bg-transparent text-muted hover:bg-card-hover hover:text-foreground',
  outline: 'bg-transparent text-foreground border border-border hover:border-border-hi',
};

const sizeClasses: Record<Size, string> = {
  sm: 'h-8 px-3 text-xs',
  md: 'h-10 px-4 text-sm',
  lg: 'h-12 px-6 text-base',
  icon: 'h-9 w-9 p-0',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'default', size = 'md', ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
      {...props}
    />
  ),
);
Button.displayName = 'Button';
