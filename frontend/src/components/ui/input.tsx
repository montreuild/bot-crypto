'use client';

/**
 * S1-F2-US1 — Composant Input stylé.
 *
 * Remplace les <input> bruts avec classes Tailwind ad hoc dispersées.
 */

import * as React from 'react';
import { cn } from '@/lib/utils';

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          'flex h-9 w-full rounded-md border border-border bg-card px-3 py-2 text-sm',
          'placeholder:text-muted',
          'focus:outline-none focus:ring-2 focus:ring-primary-400 focus:border-primary-400',
          'disabled:cursor-not-allowed disabled:opacity-50',
          'transition-colors',
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  }
);
Input.displayName = 'Input';

export { Input };
