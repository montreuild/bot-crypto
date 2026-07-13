// SmcChart — helpers de chart Smart Money Concepts PARTAGÉS (UI-10).
// Source unique de la primitive de zones (rectangles ombrés) et de la
// palette, pour que Smart graph et Smart replay ne divergent plus (ex.
// clipping des bords hors-écran). Requiert lightweight-charts chargé par
// la page AVANT ce script. Chargé uniquement par les deux pages qui
// l'utilisent réellement (smartgraph.html, smartreplay.html) — pas par
// base.html : les 11+ autres pages n'ont jamais appelé window.SmcChart.
window.SmcChart = (function(){
  const FILL = {
    demand:   ['rgba(52,211,153,.14)',  'rgba(52,211,153,.55)'],
    supply:   ['rgba(251,113,133,.14)', 'rgba(251,113,133,.55)'],
    pool:     ['rgba(147,197,253,.18)', 'rgba(147,197,253,.6)'],
    fvg:      ['rgba(167,139,250,.12)', 'rgba(167,139,250,.45)'],
    void:     ['rgba(148,163,184,.10)', 'rgba(148,163,184,.4)'],
    breaker:  ['rgba(249,115,22,.12)',  'rgba(249,115,22,.5)'],
    rejection:['rgba(244,114,182,.12)', 'rgba(244,114,182,.5)'],
  };
  // Primitive plugin (lightweight-charts v4) : dessine des rectangles [t1,t2]×
  // [bottom,top]. Clip cohérent des bords hors fenêtre visible via la direction.
  class ZonesPrimitive {
    constructor(){ this._zones=[]; this._chart=null; this._series=null; this._req=null;
      this._view={ renderer:()=>({draw:t=>this._draw(t)}), zOrder:()=>'bottom' }; }
    attached({chart,series,requestUpdate}){ this._chart=chart; this._series=series; this._req=requestUpdate; }
    detached(){ this._chart=null; this._series=null; this._req=null; }
    setZones(z){ this._zones=z||[]; if(this._req) this._req(); }
    updateAllViews(){}
    paneViews(){ return [this._view]; }
    _draw(target){
      if(!this._chart||!this._series) return;
      const ts=this._chart.timeScale();
      target.useMediaCoordinateSpace(({context:ctx, mediaSize})=>{
        const W=mediaSize.width;
        this._zones.forEach(z=>{
          let x1=ts.timeToCoordinate(z.t1);
          let x2=ts.timeToCoordinate(z.t2);
          const y1=this._series.priceToCoordinate(z.top);
          const y2=this._series.priceToCoordinate(z.bottom);
          if(y1==null||y2==null) return;
          if(x1==null && x2==null){
            const vr=ts.getVisibleRange();
            if(!vr||z.t2<vr.from||z.t1>vr.to) return;
            x1=0; x2=W;
          } else {
            if(x1==null) x1=(z.t1<=z.t2?0:W);
            if(x2==null) x2=W;
          }
          if(x2<=x1) return;
          ctx.fillStyle=z.fill;
          ctx.fillRect(x1, Math.min(y1,y2), x2-x1, Math.abs(y2-y1)||1);
          ctx.strokeStyle=z.border; ctx.lineWidth=1;
          ctx.strokeRect(x1, Math.min(y1,y2), x2-x1, Math.abs(y2-y1)||1);
          if(z.label && x2-x1>46){
            ctx.fillStyle=z.border; ctx.font='600 10px system-ui, sans-serif';
            ctx.fillText(z.label, x1+5, Math.min(y1,y2)+12);
          }
        });
      });
    }
  }
  return { FILL, ZonesPrimitive };
})();
