/**
 * Primitive lightweight-charts v4 : rectangles de zones SMC (équivalent
 * historique de `app/web/static/js/smc-chart.js` / SmcChart.ZonesPrimitive).
 *
 * Dessine des zones [t1,t2] × [bottom,top] avec fill + bordure, clip hors
 * fenêtre visible. Attachée via `series.attachPrimitive(primitive)`.
 */

import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';

export type SmcZoneKind =
  | 'demand'
  | 'supply'
  | 'pool'
  | 'fvg'
  | 'void'
  | 'breaker'
  | 'rejection'
  | 'entry';

export interface SmcZone {
  /** Epoch secondes (ou Time LWC). */
  t1: number;
  t2: number;
  top: number;
  bottom: number;
  kind: SmcZoneKind;
  label?: string;
  /** Override fill (sinon palette FILL). */
  fill?: string;
  border?: string;
}

/** Palette alignée sur l'ancienne UI Jinja2. */
export const SMC_ZONE_FILL: Record<SmcZoneKind, [string, string]> = {
  demand: ['rgba(52,211,153,.16)', 'rgba(52,211,153,.65)'],
  supply: ['rgba(251,113,133,.16)', 'rgba(251,113,133,.65)'],
  pool: ['rgba(147,197,253,.18)', 'rgba(147,197,253,.65)'],
  fvg: ['rgba(167,139,250,.18)', 'rgba(167,139,250,.7)'],
  void: ['rgba(148,163,184,.14)', 'rgba(148,163,184,.55)'],
  breaker: ['rgba(249,115,22,.16)', 'rgba(249,115,22,.65)'],
  rejection: ['rgba(244,114,182,.18)', 'rgba(244,114,182,.7)'],
  entry: ['rgba(56,189,248,.14)', 'rgba(56,189,248,.7)'],
};

export function zoneStyle(kind: SmcZoneKind, open = true): { fill: string; border: string } {
  const [fill, border] = SMC_ZONE_FILL[kind] || SMC_ZONE_FILL.fvg;
  if (open) return { fill, border };
  // Zones invalidées / filled : plus transparentes
  return {
    fill: fill.replace(/[\d.]+\)$/, (m) => `${Math.max(0.04, parseFloat(m) * 0.45)})`),
    border: border.replace(/[\d.]+\)$/, (m) => `${Math.max(0.2, parseFloat(m) * 0.55)})`),
  };
}

type Attached = {
  chart: IChartApi;
  series: ISeriesApi<any>;
  requestUpdate: () => void;
};

/**
 * Primitive pane : dessine les zones en canvas sous les bougies (zOrder bottom).
 * Compatible API plugin LWC v4 (`attachPrimitive` / `detachPrimitive`).
 */
export class ZonesPrimitive {
  private _zones: SmcZone[] = [];
  private _attached: Attached | null = null;
  private _view = {
    renderer: () => ({
      draw: (target: any) => this._draw(target),
    }),
    zOrder: () => 'bottom' as const,
  };

  attached(param: Attached) {
    this._attached = param;
  }

  detached() {
    this._attached = null;
  }

  setZones(zones: SmcZone[]) {
    this._zones = zones || [];
    this._attached?.requestUpdate();
  }

  updateAllViews() {
    /* no-op — redraw via requestUpdate */
  }

  paneViews() {
    return [this._view];
  }

  private _draw(target: any) {
    const att = this._attached;
    if (!att) return;
    const { chart, series } = att;
    const ts = chart.timeScale();
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }: any) => {
      const W = Number(mediaSize.width) || 0;
      for (const z of this._zones) {
        const rawX1 = ts.timeToCoordinate(z.t1 as Time);
        const rawX2 = ts.timeToCoordinate(z.t2 as Time);
        const rawY1 = series.priceToCoordinate(z.top);
        const rawY2 = series.priceToCoordinate(z.bottom);
        if (rawY1 == null || rawY2 == null) continue;

        let x1: number;
        let x2: number;
        if (rawX1 == null && rawX2 == null) {
          const vr = ts.getVisibleRange();
          if (!vr || z.t2 < (vr.from as number) || z.t1 > (vr.to as number)) continue;
          x1 = 0;
          x2 = W;
        } else {
          x1 = rawX1 == null ? (z.t1 <= z.t2 ? 0 : W) : Number(rawX1);
          x2 = rawX2 == null ? W : Number(rawX2);
        }
        if (!(x2 > x1)) continue;

        const fill = z.fill ?? zoneStyle(z.kind).fill;
        const border = z.border ?? zoneStyle(z.kind).border;
        const top = Math.min(Number(rawY1), Number(rawY2));
        const h = Math.abs(Number(rawY2) - Number(rawY1)) || 1;

        ctx.fillStyle = fill;
        ctx.fillRect(x1, top, x2 - x1, h);
        ctx.strokeStyle = border;
        ctx.lineWidth = 1.5;
        ctx.strokeRect(x1, top, x2 - x1, h);

        if (z.label && x2 - x1 > 46) {
          ctx.fillStyle = border;
          ctx.font = '600 10px system-ui, sans-serif';
          ctx.fillText(z.label, x1 + 5, top + 12);
        }
      }
    });
  }
}

/** Helper : attache (ou réutilise) une primitive zones sur une série. */
export function ensureZonesPrimitive(
  series: ISeriesApi<any> & { attachPrimitive?: (p: any) => void; detachPrimitive?: (p: any) => void },
  existing: ZonesPrimitive | null,
): ZonesPrimitive {
  if (existing) return existing;
  const prim = new ZonesPrimitive();
  if (typeof series.attachPrimitive === 'function') {
    series.attachPrimitive(prim);
  }
  return prim;
}

/** Construit la liste de zones à partir d'un payload SMC (+ toggles). */
export function buildZonesFromSmc(
  data: any,
  opts: {
    orderBlocks?: boolean;
    fvg?: boolean;
    liquidityVoids?: boolean;
    breakers?: boolean;
    rejectionBlocks?: boolean;
    firstTime?: number;
    lastTime?: number;
  } = {},
): SmcZone[] {
  const zones: SmcZone[] = [];
  const ft = opts.firstTime;
  const lt = opts.lastTime;

  const pushRect = (
    items: any[] | undefined,
    kind: SmcZoneKind,
    openStatuses: string[],
    labelPrefix: string,
  ) => {
    if (!Array.isArray(items)) return;
    for (const it of items) {
      const top = Number(it.top);
      const bottom = Number(it.bottom);
      if (!Number.isFinite(top) || !Number.isFinite(bottom)) continue;
      const t1 = Number(it.time_start ?? it.t1 ?? ft);
      const t2 = Number(it.time_end ?? it.t2 ?? lt);
      if (!Number.isFinite(t1) || !Number.isFinite(t2)) continue;
      const status = String(it.status || 'open');
      const open = openStatuses.length === 0 || openStatuses.includes(status);
      const bullish = it.kind === 'bullish' || it.kind === 'buyside' || it.kind === 'demand';
      const resolvedKind: SmcZoneKind =
        kind === 'demand' || kind === 'supply'
          ? (bullish ? 'demand' : 'supply')
          : kind;
      const st = zoneStyle(resolvedKind, open);
      zones.push({
        t1: Math.min(t1, t2),
        t2: Math.max(t1, t2),
        top,
        bottom,
        kind: resolvedKind,
        fill: st.fill,
        border: st.border,
        label: labelPrefix,
      });
    }
  };

  if (opts.orderBlocks !== false) {
    pushRect(data?.order_blocks, 'demand', ['fresh', 'touched', 'open'], 'OB');
  }
  if (opts.fvg !== false) {
    pushRect(data?.fvgs, 'fvg', ['open', 'mitigated'], 'FVG');
  }
  if (opts.liquidityVoids !== false) {
    pushRect(data?.liquidity_voids ?? data?.voids, 'void', ['open', 'mitigated'], 'VOID');
  }
  if (opts.breakers) {
    pushRect(data?.breakers, 'breaker', ['fresh', 'touched', 'open'], 'BRK');
  }
  if (opts.rejectionBlocks !== false) {
    pushRect(data?.rejection_blocks ?? data?.rejections, 'rejection', ['fresh', 'touched', 'open'], 'REJ');
  }
  return zones;
}
