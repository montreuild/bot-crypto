/**
 * BT-008 — Panneau de statistiques agrégées des trades.
 *
 * Extrait du template Jinja2 `backtest.html:861-926` (chips + tableaux
 * par setup et par raison de sortie).
 *
 * L0–L6 : les axes d'analyse ne sont plus codés un par un. Chaque champ que le
 * moteur pose sur un trade — setup, module, état de structure, séquence, tier,
 * classe de cible — devient un tableau dès qu'il est renseigné, et disparaît
 * quand il ne l'est pas. Une stratégie qui n'en pose aucun retrouve exactement
 * l'affichage d'avant.
 */

import { useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { exitReasonBadge } from '@/lib/exit-reason-badges';
import type { BacktestTrade } from '@/types';

interface Props {
  trades: BacktestTrade[];
}

interface Agrege {
  n: number;
  wins: number;
  pnl: number;
}

/** Axes d'analyse : libellé + extracteur. L'ordre est celui de l'affichage. */
const AXES: Array<{
  cle: string;
  titre: string;
  couleur: string;
  valeur: (t: BacktestTrade) => string | undefined;
}> = [
  { cle: 'setup', titre: 'Par setup', couleur: 'text-purple-400', valeur: (t) => t.setup },
  { cle: 'module', titre: 'Par module SMC/ICT (§65)', couleur: 'text-indigo-400', valeur: (t) => t.module },
  {
    cle: 'structure',
    titre: 'Par état de structure (§60)',
    couleur: 'text-sky-400',
    valeur: (t) => t.structure_state,
  },
  {
    cle: 'sequence',
    titre: 'Par type de séquence (§72)',
    couleur: 'text-teal-400',
    valeur: (t) => t.sequence_type,
  },
  { cle: 'tier', titre: 'Par tier (§71)', couleur: 'text-amber-400', valeur: (t) => t.tier },
  {
    cle: 'cible',
    titre: 'Par classe de cible (§77)',
    couleur: 'text-lime-400',
    valeur: (t) => (t.indicators as Record<string, unknown> | undefined)?.tp_class as string | undefined,
  },
];

function agreger(
  trades: BacktestTrade[],
  cle: (t: BacktestTrade) => string | undefined,
): Array<[string, Agrege]> {
  const out: Record<string, Agrege> = {};
  trades.forEach((t) => {
    const k = cle(t);
    if (!k) return;
    if (!out[k]) out[k] = { n: 0, wins: 0, pnl: 0 };
    out[k].n++;
    if ((t.pnl ?? 0) > 0) out[k].wins++;
    out[k].pnl += t.pnl ?? 0;
  });
  return Object.entries(out).sort((a, b) => b[1].pnl - a[1].pnl);
}

export function TradesStatsPanel({ trades }: Props) {
  const stats = useMemo(() => {
    const closed = trades.filter((t) => t.status !== 'open');
    const longs = closed.filter((t) => t.side === 'long');
    const shorts = closed.filter((t) => t.side === 'short');
    const wins = closed.filter((t) => (t.pnl ?? 0) > 0);
    const losses = closed.filter((t) => (t.pnl ?? 0) <= 0);
    const longWins = longs.filter((t) => (t.pnl ?? 0) > 0);
    const shortWins = shorts.filter((t) => (t.pnl ?? 0) > 0);
    const avgWin = wins.length ? wins.reduce((s, t) => s + (t.pnl ?? 0), 0) / wins.length : null;
    const avgLoss = losses.length ? losses.reduce((s, t) => s + (t.pnl ?? 0), 0) / losses.length : null;
    const wrLong = longs.length ? (longWins.length / longs.length) * 100 : null;
    const wrShort = shorts.length ? (shortWins.length / shorts.length) * 100 : null;

    // L1 — combien de trades ont réellement sorti une jambe avant la clôture ?
    // Sans ce chiffre, on ne sait pas si `use_partial_exits` a mordu.
    const avecJambes = closed.filter((t) => (t.exits?.length ?? 0) > 0).length;
    const jambes = closed.reduce((s, t) => s + (t.exits?.length ?? 0), 0);

    const byExit = agreger(closed, (t) => t.exit_reason ?? t.reason ?? '—');

    // §5 — PnL PAR JAMBE de sortie. `byExit` compte des trades ; il ne dit rien
    // de la façon dont un trade fractionné a gagné son argent. C'est pourtant la
    // question que pose un mode TP1/TP2/runner : le reliquat paie-t-il les
    // jambes prises tôt, ou sont-ce elles qui sauvent un reliquat perdant ?
    //
    // Le reliquat n'est pas journalisé comme une jambe (il part avec la clôture
    // du trade) : on le reconstitue par différence, ce qui garantit que la somme
    // des lignes redonne le PnL des trades fractionnés.
    const parJambe = new Map<string, { n: number; wins: number; pnl: number }>();
    const cumule = (cle: string, pnl: number) => {
      const d = parJambe.get(cle) ?? { n: 0, wins: 0, pnl: 0 };
      d.n += 1;
      d.pnl += pnl;
      if (pnl > 0) d.wins += 1;
      parJambe.set(cle, d);
    };
    for (const t of closed) {
      const legs = t.exits ?? [];
      if (legs.length) {
        let cumulLegs = 0;
        for (const leg of legs) {
          const pnl = leg.pnl ?? 0;
          cumulLegs += pnl;
          cumule(leg.reason ?? 'jambe', pnl);
        }
        cumule('runner', (t.pnl ?? 0) - cumulLegs);
      } else {
        // Trade sorti EN UNE FOIS. L'inclure n'est pas un détail : sans lui, le
        // découpage ne couvrait que les trades fractionnés — tous gagnants par
        // construction, puisqu'une jambe ne se déclenche qu'en atteignant sa
        // cible. Mesuré sur ETH/USDC 4 h : 80 trades fractionnés à +1 902
        // affichés, 89 non fractionnés à −1 629 invisibles, et un tableau qui
        // annonçait 695 % du PnL réel avec 100 % de réussite partout.
        cumule(`complet · ${t.exit_reason ?? t.reason ?? 'inconnu'}`, t.pnl ?? 0);
      }
    }
    const totalAbs =
      [...parJambe.values()].reduce((s2, d) => s2 + Math.abs(d.pnl), 0) || 1;
    const byLeg = [...parJambe.entries()]
      .map(([cle, d]) => ({
        cle,
        n: d.n,
        pnl: d.pnl,
        wr: d.n ? (d.wins / d.n) * 100 : 0,
        part: (Math.abs(d.pnl) / totalAbs) * 100,
      }))
      .sort((x, y) => Math.abs(y.pnl) - Math.abs(x.pnl));

    return {
      longs: longs.length,
      shorts: shorts.length,
      wrLong,
      wrShort,
      avgWin,
      avgLoss,
      avecJambes,
      jambes,
      byExit,
      byLeg,
      axes: AXES.map((a) => ({ ...a, lignes: agreger(closed, a.valeur) })).filter(
        (a) => a.lignes.length > 0,
      ),
    };
  }, [trades]);

  const chip = (label: string, val: string | number, color = '') => (
    <div className="bg-surface border border-border rounded-md px-3 py-1.5 min-w-[90px]">
      <div className="text-[0.55rem] uppercase tracking-wide text-muted-foreground font-semibold mb-0.5">
        {label}
      </div>
      <div className={`font-mono text-sm font-bold ${color}`}>{val}</div>
    </div>
  );

  const tableau = (
    titre: string,
    couleur: string,
    lignes: Array<[string, Agrege]>,
    rendu?: (k: string) => React.ReactNode,
  ) => (
    <div key={titre}>
      <div className="text-xs text-muted-foreground mb-1">{titre}</div>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-muted-foreground">
            <th className="text-left py-1">Groupe</th>
            <th className="text-right py-1 px-2">N</th>
            <th className="text-right py-1 px-2">WR%</th>
            <th className="text-right py-1 px-2">PnL total</th>
          </tr>
        </thead>
        <tbody>
          {lignes.map(([k, s]) => (
            <tr key={k} className="border-b border-border/50">
              <td className={`py-1 font-mono ${couleur}`}>{rendu ? rendu(k) : k}</td>
              <td className="text-right py-1 px-2 font-mono">{s.n}</td>
              <td className="text-right py-1 px-2 font-mono">
                {((s.wins / s.n) * 100).toFixed(1)}%
              </td>
              <td
                className={`text-right py-1 px-2 font-mono ${
                  s.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                }`}
              >
                {s.pnl >= 0 ? '+' : ''}${s.pnl.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">📊 Statistiques des trades</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {chip('Long', stats.longs, 'text-cyan-400')}
          {chip('Short', stats.shorts, 'text-rose-400')}
          {chip('WR Long', stats.wrLong != null ? `${stats.wrLong.toFixed(1)}%` : '—')}
          {chip('WR Short', stats.wrShort != null ? `${stats.wrShort.toFixed(1)}%` : '—')}
          {chip('Avg Win', stats.avgWin != null ? `$${stats.avgWin.toFixed(2)}` : '—', 'text-emerald-400')}
          {chip('Avg Loss', stats.avgLoss != null ? `$${stats.avgLoss.toFixed(2)}` : '—', 'text-rose-400')}
          {stats.jambes > 0 &&
            chip('Sorties partielles', `${stats.jambes} / ${stats.avecJambes} tr.`, 'text-amber-400')}
        </div>

        {stats.axes.map((a) => tableau(a.titre, a.couleur, a.lignes))}

        {stats.byLeg.length > 0 && (
          <div>
            <div className="text-xs text-muted-foreground mb-1">
              Par poste de sortie (§5) — d&apos;où vient le PnL (somme = PnL total)
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="text-left py-1">Jambe</th>
                  <th className="text-right py-1 px-2">N</th>
                  <th className="text-right py-1 px-2">WR%</th>
                  <th className="text-right py-1 px-2">PnL</th>
                  <th className="text-right py-1 px-2">Part</th>
                </tr>
              </thead>
              <tbody>
                {stats.byLeg.map((l) => (
                  <tr key={l.cle} className="border-b border-border/50">
                    <td className="py-1 font-mono text-amber-400">{l.cle}</td>
                    <td className="text-right py-1 px-2 font-mono">{l.n}</td>
                    <td className="text-right py-1 px-2 font-mono">{l.wr.toFixed(1)}%</td>
                    <td
                      className={`text-right py-1 px-2 font-mono ${
                        l.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'
                      }`}
                    >
                      {l.pnl >= 0 ? '+' : ''}${l.pnl.toFixed(2)}
                    </td>
                    <td className="text-right py-1 px-2 font-mono text-muted-foreground">
                      {l.part.toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {stats.byExit.length > 0 &&
          tableau('Par raison de sortie', '', stats.byExit, (k) => {
            const badge = exitReasonBadge(k);
            return (
              <span
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[0.65rem] font-mono"
                style={{
                  color: badge.color,
                  background: `${badge.color}15`,
                  border: `1px solid ${badge.color}33`,
                }}
              >
                {badge.emoji} {badge.abbr}
              </span>
            );
          })}
      </CardContent>
    </Card>
  );
}
