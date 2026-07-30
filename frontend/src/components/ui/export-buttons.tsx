'use client';

/**
 * S9-F4-US3/4/5 — Boutons d'export réutilisables.
 *
 * Fournit :
 *  - exportJson(filename, data) : téléchargement JSON formaté
 *  - exportCsv(filename, rows, headers) : génération CSV côté client
 *  - ExportPdfButton : placeholder pour future implémentation jsPDF
 *
 * Utilisé par :
 *  - Page backtest (export JSON complet du résultat)
 *  - Page audit (export CSV)
 *  - Page compare (export CSV)
 */

import { Button } from '@/components/ui/button';
import { Download, FileJson, FileText, FileSpreadsheet, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

/**
 * Télécharge un blob avec le filename donné.
 */
function downloadBlob(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Date au format YYYY-MM-DD pour les filenames.
 */
function dateStamp(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * Export JSON — formate et télécharge.
 */
export function exportJson(filename: string, data: any) {
  try {
    const json = JSON.stringify(data, null, 2);
    downloadBlob(json, `${filename}_${dateStamp()}.json`, 'application/json');
    toast.success(`Export JSON téléchargé : ${filename}_${dateStamp()}.json`);
  } catch (e: any) {
    toast.error(`Erreur export JSON : ${e.message}`);
  }
}

/**
 * Export CSV — génère depuis un tableau d'objets.
 * `headers` : { key: label } pour contrôler l'ordre et les libellés.
 */
export function exportCsv(filename: string, rows: any[], headers: Record<string, string>) {
  try {
    const headerRow = Object.values(headers).join(',');
    const dataRows = rows.map((row) =>
      Object.keys(headers)
        .map((key) => {
          const val = row[key];
          if (val == null) return '';
          // Escape quotes et wrap si contient virgule/quote/newline
          const str = String(val);
          if (str.includes(',') || str.includes('"') || str.includes('\n')) {
            return `"${str.replace(/"/g, '""')}"`;
          }
          return str;
        })
        .join(',')
    );
    const csv = [headerRow, ...dataRows].join('\n');
    downloadBlob(csv, `${filename}_${dateStamp()}.csv`, 'text/csv;charset=utf-8');
    toast.success(`Export CSV téléchargé : ${filename}_${dateStamp()}.csv`);
  } catch (e: any) {
    toast.error(`Erreur export CSV : ${e.message}`);
  }
}

// ── Composants boutons ────────────────────────────────────────────────────

interface JsonExportButtonProps {
  filename: string;
  data: any;
  disabled?: boolean;
  label?: string;
}

export function JsonExportButton({ filename, data, disabled, label = 'JSON' }: JsonExportButtonProps) {
  const [loading, setLoading] = useState(false);
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={disabled || loading || !data}
      onClick={() => {
        setLoading(true);
        try {
          exportJson(filename, data);
        } finally {
          setTimeout(() => setLoading(false), 500);
        }
      }}
    >
      {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <FileJson className="w-3 h-3" />}
      ↓ {label}
    </Button>
  );
}

interface CsvExportButtonProps {
  filename: string;
  rows: any[];
  headers: Record<string, string>;
  disabled?: boolean;
  label?: string;
}

export function CsvExportButton({ filename, rows, headers, disabled, label = 'CSV' }: CsvExportButtonProps) {
  const [loading, setLoading] = useState(false);
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={disabled || loading || !rows || rows.length === 0}
      onClick={() => {
        setLoading(true);
        try {
          exportCsv(filename, rows, headers);
        } finally {
          setTimeout(() => setLoading(false), 500);
        }
      }}
    >
      {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <FileSpreadsheet className="w-3 h-3" />}
      ↓ {label}
    </Button>
  );
}

/**
 * S9-F4-US4 — Export PDF (placeholder).
 * Implémentation future : jsPDF + screenshot chart via html2canvas.
 */
export function PdfExportButton({ filename, disabled, label = 'PDF' }: { filename: string; disabled?: boolean; label?: string }) {
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={disabled}
      onClick={() => toast.info('Export PDF — à implémenter (jsPDF + html2canvas)')}
      title="Bientôt disponible"
    >
      <FileText className="w-3 h-3" />
      ↓ {label}
    </Button>
  );
}
