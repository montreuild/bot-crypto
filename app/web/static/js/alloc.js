// renderAllocGrid — affichage partagé de la répartition du capital par slot
// (UI-08). Deux styles préservant EXACTEMENT le rendu historique de chacune
// des pages qui l'utilisaient (aucun changement visuel, juste dédup du JS) :
//
//   opts.style = 'card' (dashboard.html) : grille de cartes cliquables
//                (→ /bots), pause/off/seuil 80% colorés.
//   opts.style = 'row'  (portfolio.html) : liste de lignes avec barre de
//                cible « shadow allocation » superposée (trait ambre).
//
// bots.html n'utilise PAS cette fonction : sa barre de budget est une seule
// ligne (`.budget-mini`) au sein d'une carte de bot bien plus riche
// (cycle de vie/edge/pnl) — un composant différent, pas une grille
// d'allocation ; l'extraire isolément n'aurait dédupliqué qu'une ligne de
// balisage pour un couplage plus complexe. Laissé local à bots.html.
function renderAllocGrid(container, slots, opts) {
  opts = opts || {};
  if (!container) return;
  if (!slots || !slots.length) {
    container.innerHTML = opts.style === 'row'
      ? '<div style="color:var(--dim);font-size:.8rem">Aucun slot actif.</div>'
      : '<span style="color:var(--dim);font-size:.75rem">Aucun slot actif</span>';
    return;
  }
  if (opts.style === 'row') {
    const shadow = opts.shadowTargets || {};
    const sdelta = opts.shadowDelta || {};
    container.innerHTML = slots.map(s => {
      const cur = s.budget_pct || 0;
      const tgt = (shadow[s.slot_key] != null) ? shadow[s.slot_key] * 100 : null;
      const dl = sdelta[s.slot_key];
      const dlTxt = (dl == null) ? '' : `<span class="${dl>=0?'delta-up':'delta-dn'}">${(dl>=0?'+':'')+(dl*100).toFixed(1)}%</span>`;
      const tgtMark = (tgt != null) ? `<div class="alloc-tgt" style="left:${Math.min(tgt,100)}%"></div>` : '';
      return `<div class="alloc-row"><div class="nm" title="${escHtml(s.slot_key)}">${escHtml(s.slot_key)}</div>
        <div class="alloc-bar"><div class="alloc-cur" style="width:${Math.min(cur,100)}%"></div>${tgtMark}</div>
        <div class="alloc-nums">${cur.toFixed(1)}% ${dlTxt}</div></div>`;
    }).join('');
    return;
  }
  // style 'card' (défaut, dashboard.html)
  const paused = {};
  (opts.slotStates || []).forEach(st => { if (st.paused) paused[st.slot_key] = {reason: st.pause_reason}; });
  container.innerHTML = slots.map(s => {
    const isOff = s.enabled === false;
    const usedPct = s.used_pct ?? 0, isPaused = !!paused[s.slot_key];
    const fillClr = isOff ? 'var(--dim)' : isPaused ? 'var(--red)' : usedPct > 80 ? 'var(--amber)' : 'var(--cyan)';
    const slotLabel = s.strategy.charAt(0).toUpperCase() + s.strategy.slice(1) + ' ' + s.tf;
    const pnlStr = s.weekly_pnl != null ? ((s.weekly_pnl >= 0 ? '+' : '') + s.weekly_pnl.toFixed(2) + ' 7j') : '';
    return `<div class="alloc-card${isPaused?' paused':''}${isOff?' disabled':''}" onclick="window.location.href='/bots'" title="Cliquez pour gérer ce bot">
      <div class="alloc-slot">${escHtml(slotLabel)}${isOff?' <span style="color:var(--dim);font-size:.65rem">OFF</span>':''}</div>
      <div class="alloc-meta">Budget: <strong style="color:var(--text)">${s.budget_pct??'—'}%</strong> · ${s.budget_usdc??'—'} USDC${pnlStr?` · <span class="${s.weekly_pnl>=0?'c-green':'c-red'}">${pnlStr}</span>`:''}${s.weekly_trades?' · '+s.weekly_trades+'T':''}</div>
      <div class="alloc-track"><div class="alloc-fill" style="width:${Math.min(usedPct,100)}%;background:${fillClr}"></div></div>
      <div class="alloc-nums"><span>${(s.used_notional??0).toFixed(1)} USDC utilisé</span><span>${usedPct.toFixed(0)}%</span></div>
      ${isPaused?`<div class="alloc-cb">⏸ ${escHtml(paused[s.slot_key].reason)}</div>`:''}
    </div>`;
  }).join('');
}
