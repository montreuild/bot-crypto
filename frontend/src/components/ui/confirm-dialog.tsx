'use client';

/**
 * S1-F2-US8 — Composant ConfirmDialog.
 *
 * Remplace window.confirm (non stylé, bloquant) par une modale accessible
 * basée sur Radix Dialog. Utilisé pour les actions critiques : promote/reject
 * ML, stop bot, reset CB, suppressions, etc.
 *
 * Usage :
 * <ConfirmDialog
 *   open={open}
 *   onOpenChange={setOpen}
 *   title="Promouvoir cette version ?"
 *   description="Cette action marquera la version comme décision manuelle..."
 *   confirmLabel="Promouvoir"
 *   variant="success"
 *   onConfirm={() => handleConfirm()}
 * />
 */

import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

type Variant = 'default' | 'danger' | 'success';

interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: Variant;
  isLoading?: boolean;
  onConfirm: () => void | Promise<void>;
}

const variantClass: Record<Variant, string> = {
  default: 'bg-primary-500 hover:bg-primary-600 text-white',
  danger: 'bg-red-600 hover:bg-red-700 text-white',
  success: 'bg-emerald-600 hover:bg-emerald-700 text-white',
};

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirmer',
  cancelLabel = 'Annuler',
  variant = 'default',
  isLoading = false,
  onConfirm,
}: ConfirmDialogProps) {
  const handleConfirm = async () => {
    await onConfirm();
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <DialogFooter className="gap-2 sm:gap-2 mt-4">
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={isLoading}
          >
            {cancelLabel}
          </Button>
          <Button
            className={cn(variantClass[variant])}
            onClick={handleConfirm}
            disabled={isLoading}
          >
            {isLoading && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
