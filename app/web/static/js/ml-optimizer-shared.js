// Fonctions partagées entre ml.html et optimizer.html (UI-05).
// Extraites de deux implémentations quasi-identiques (copier-coller
// d'origine) : ne couvre QUE les fonctions restées comportementalement
// identiques entre les deux pages. Plusieurs autres fonctions du même
// paneau (renderJobs, renderJobCard, renderSpaces, applyJob* variantes,
// startOpt, updatePreviewMatrix, renderStratChecks…) ont depuis divergé
// avec de vraies différences fonctionnelles propres à chaque page (ex.
// filtrage stratégies ML vs toutes, affichage alpha, messages dédiés) —
// les fusionner changerait le comportement de l'une des deux pages, donc
// elles restent volontairement définies localement à chaque template.
//
// Dépend de apiFetch/toast/escHtml (base.html) et des globals page-locaux
// _lastJobs/_expandedJobs/refreshJobs (déclarés dans le <script> inline de
// chaque page, chargé après ce fichier).

const TF_INFO = {
  '5m':  {label:'5m',  def:4000,  max:8000, note:'~14j · max ~8000'},
  '15m': {label:'15m', def:2000,  max:8000, note:'~21j · max ~8000'},
  '30m': {label:'30m', def:1500,  max:8000, note:'~31j · max ~8000'},
  '1h':  {label:'1h',  def:1500,  max:8000, note:'~62j · max ~8000'},
  '4h':  {label:'4h',  def:800,   max:2000, note:'~133j · max ~2000'},
  '1d':  {label:'1d',  def:2000,  max:3000, note:'~2000j · max ~2500'},
};
// Liste des TFs sélectionnables — pilotée par la config à chaud (trading.timeframes).
let AVAILABLE_TFS = ['1h'];

function tfMeta(tf) { return TF_INFO[tf] || {label:tf, def:1500, max:8000, note:''}; }

// accentRgb : triplet "R,G,B" de la couleur d'accent de la page (chaque
// page garde SA couleur historique — cyan par défaut, optimizer.html
// passe explicitement le bleu qu'il utilisait déjà avant extraction).
function toggleTfCheck(lbl, tf, accentRgb) {
  const rgb = accentRgb || '34,211,238';
  const cb = document.getElementById('tfc-' + tf);
  cb.checked = !cb.checked;
  const on = cb.checked;
  lbl.style.borderColor  = on ? `rgba(${rgb},.4)` : 'var(--border)';
  lbl.style.background   = on ? 'var(--cyan-dim)' : 'transparent';
  lbl.querySelector('span').style.color = on ? 'var(--cyan)' : 'var(--muted)';
  updateStratCompatibility();
  updateLimHint();
}

function renderTfChecks(activeTfs, accentRgb) {
  const rgb = accentRgb || '34,211,238';
  AVAILABLE_TFS = (activeTfs && activeTfs.length) ? activeTfs.slice() : ['1h'];
  const el = document.getElementById('tf-checks');
  el.innerHTML = AVAILABLE_TFS.map((tf) => {
    const info = tfMeta(tf);
    const checked = true;
    return `<label style="cursor:pointer;display:inline-flex;align-items:center;gap:5px;
        padding:5px 10px;border-radius:6px;border:1px solid ${checked?`rgba(${rgb},.4)`:'var(--border)'};
        background:${checked?'var(--cyan-dim)':'transparent'};transition:.15s"
        onclick="toggleTfCheck(this,'${tf}','${rgb}')">
      <input type="checkbox" id="tfc-${tf}" ${checked?'checked':''} style="display:none">
      <span style="font-weight:700;color:${checked?'var(--cyan)':'var(--muted)'}">${info.label}</span>
      <span style="font-size:.65rem;color:var(--dim)">${info.note}</span>
    </label>`;
  }).join('');
}

async function cancelJob(jobId, e) {
  const btn = e ? e.target : null;
  if (btn) { btn.disabled = true; btn.textContent = '⏳…'; }
  try {
    const r = await apiFetch(`/api/optimize/cancel?job_id=${encodeURIComponent(jobId)}`, {method:'POST'});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    toast('Signal d\'annulation envoyé — arrêt en cours…');
    refreshJobs();
  } catch(err) {
    if (btn) { btn.disabled = false; btn.textContent = '✕ Annuler'; }
    toast('Erreur: ' + err.message, true);
  }
}

async function applyJob(jobId, e) {
  const btn = e ? e.target : null;
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = '⏳ Application…';
  try {
    const r = await apiFetch(`/api/optimize/apply?job_id=${encodeURIComponent(jobId)}`, {method:'POST'});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    if (_lastJobs[jobId]) _lastJobs[jobId].applied = true;
    refreshJobs();
  } catch(e) {
    btn.disabled = false;
    btn.textContent = '✓ Appliquer dans config.yaml';
    alert('Erreur: ' + e.message);
  }
}

async function deleteJob(jobId, e) {
  const btn = e ? e.target : null;
  if (btn) { btn.disabled = true; }
  try {
    const r = await apiFetch(`/api/optimize/job?job_id=${encodeURIComponent(jobId)}`, {method:'DELETE'});
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    if (_lastJobs) delete _lastJobs[jobId];
    _expandedJobs.delete(jobId);
    renderJobs(_lastJobs || {});
    toast('Job supprimé');
  } catch(err) {
    if (btn) { btn.disabled = false; }
    toast('Erreur: ' + err.message, true);
  }
}
