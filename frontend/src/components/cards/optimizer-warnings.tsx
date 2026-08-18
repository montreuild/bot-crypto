/**
 * OPT-003 — Warnings anti-surapprentissage pour les job cards de l'optimiseur.
 *
 * Extrait du template Jinja2 `optimizer.html:712-723`.
 * 3 warnings : overfit > 2, trades OOS < 3, score OOS < -0.05.
 */

interface Props {
  overfit: number | null | undefined;
  oosTrades: number | null | undefined;
  oosScore: number | null | undefined;
}

export function OptimizerWarnings({ overfit, oosTrades, oosScore }: Props) {
  const warnings: Array<{ level: 'warn' | 'critical'; text: string }> = [];

  if (overfit != null && overfit > 2) {
    warnings.push({
      level: 'warn',
      text: `⚠ Overfit détecté (${overfit.toFixed(2)}) — la performance IS est largement supérieure à l'OOS. Les params optimisés risquent de ne pas se généraliser.`,
    });
  }

  if (oosTrades != null && oosTrades < 3) {
    warnings.push({
      level: 'warn',
      text: `⚠ Seulement ${oosTrades} trades sur la période OOS — échantillon insuffisant pour valider l'edge.`,
    });
  }

  if (oosScore != null && oosScore < -0.05) {
    warnings.push({
      level: 'critical',
      text: `🚫 Score OOS < -0.05 — stratégie **exclue du live trading** même si vous appliquez les params.`,
    });
  }

  if (warnings.length === 0) return null;

  return (
    <div className="space-y-2">
      {warnings.map((w, i) => (
        <div
          key={w.text}
          className={`rounded-md px-3 py-2 text-xs ${
            w.level === 'critical'
              ? 'border border-rose-500/30 bg-rose-500/10 text-rose-300'
              : 'border border-amber-500/30 bg-amber-500/10 text-amber-300'
          }`}
        >
          {w.text.split(/\*\*(.+?)\*\*/).map((part, j) =>
            j % 2 === 1 ? <strong key={j}>{part}</strong> : part,
          )}
        </div>
      ))}
    </div>
  );
}
