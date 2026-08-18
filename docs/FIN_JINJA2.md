# 🪦 Acte officiel de fin de Jinja2 — Bot-Crypto V12

> **Date d'effet** : 29 juillet 2026 (Sprint 0)
> **Date de suppression physique** : 29 juillet 2026 — **✅ FAIT (S6-09)**
> **Décision** : D4 — voir `docs/PLAN_DIRECTEUR_AMELIORATIONS.md`

## ✅ Statut : SUPPRIMÉ PHYSIQUEMENT

La suppression physique a été réalisée dans le commit S6-09 (29/07/2026) —
**en avance sur le planning** (prévu fin Sprint 6, fait dès le Sprint 5/6).
Le frontend Next.js est désormais l'unique frontend officiel.

### Détail de la suppression

| Action | Statut |
|---|---|
| Suppression de `app/web/templates/` (19 fichiers HTML dont `base.html`, ~10 600 lignes) | ✅ Fait |
| Suppression de `app/web/static/` (JS/CSS partagés) | ✅ Fait |
| Suppression des routes HTML (`@app.get("/")`, `/backtest`, … ) dans `app/api/main.py` | ✅ Fait |
| Remplacement par redirects 308 permanents vers `FRONTEND_URL` (Next.js) | ✅ Fait |
| Suppression des imports `Jinja2Templates`, `StaticFiles`, `HTMLResponse` | ✅ Fait |
| Documentation mise à jour (`README.md`, `requirements.txt`, ce fichier) | ✅ Fait |
| Retrait de `jinja2==3.1.6` du `requirements.txt` direct | ✅ Conservé comme transitive (FastAPI docs) |
| Validation build Next.js 23 pages | ✅ Fait (15.4s, 0 erreur) |
| Validation syntaxe Python (`app/api/main.py`) | ✅ Fait |
| Routes API REST (`/api/*`) intactes | ✅ Aucune cassure |

> ⚠ Précision : l'ancien FastAPI servait le dashboard sur `/`. La route
> `@app.get("/dashboard")` **n'a jamais existé** côté backend — les
> commentaires de `app/api/main.py` qui la citent en exemple sont
> historiquement inexacts. `HTML_ROUTES_TO_REDIRECT` est bien exhaustif.

### Audit de complétude (29/07/2026, commit `0101fe9`)

Migration vérifiée **complète** : parité 18 pages ↔ 18 routes Next.js, 0
orphelin, 0 import Jinja2 résiduel, `build` 23/23 pages, `type-check` 0 erreur,
22 tests API verts. Le détail des contrôles et les 4 écarts trouvés (aucun
bloquant) sont dans `docs/PLAN_DIRECTEUR_AMELIORATIONS.md`
§ « Vérification post-migration » → items **S6-11 / S6-12 / S6-13**.

---

## Décision

Le frontend **Next.js** (`frontend/`) devient le **frontend officiel
unique** de bot-crypto. Les templates **Jinja2** (`app/web/templates/`)
sont **décommissionnés** et seront **supprimés physiquement** à la fin du
Sprint 6, après validation E2E Playwright des 17 pages Next.js.

## Raisons

1. **Dualité frontend coûteuse** — Le repo maintient deux frontends en
   parallèle depuis la migration Next.js (Vue V12). Cela double l'effort
   de maintenance, crée des bugs UI dupliqués (UI-01 à UI-12 documentés
   dans `docs/audit/06-ui-ux.md`), et empêche d'investir dans un design
   system unique.

2. **Endettement technique** — Les templates Jinja2 cumulent ~10 600
   lignes avec duplication massive (`scanner.html` 1407 L, `config.html`
   1423 L, `backtest.html` 1092 L, etc.). Le `base.html` mutualise quelques
   helpers (`escHtml`, `apiFetch`, `toast`) mais le JS reste inline sans
   framework, ce qui rend l'accessibilité et la performance difficiles.

3. **Bugs P1 critiques** — 4 bugs P1 identifiés par l'audit UI/UX
   (`UI-01` XSS, `UI-02` config mono-symbole, `UI-03` audit écrase OOS,
   `UI-04` trades filtre Slot) restent ouverts sur Jinja2. Corriger sur
   Jinja2 n'aurait aucun sens si le frontend est migré sous 4 mois.

4. **Maturité Next.js** — Le frontend Next.js 15 / React 19 / TanStack /
   Radix / Tailwind est déjà en place, structuré (sidebar, layouts,
   providers), et offre une UX moderne (skeletons, optimistic UI, PWA,
   i18n). Sa stack est cohérente avec les standards 2025.

5. **Performance** — Next.js 15 avec SSR + RSC (React Server Components)
   offre TTFB et TTI bien meilleurs que des templates Jinja2 servis en
   HTML statique avec JS inline, surtout sur mobile.

## Plan de suppression

### Étape 1 — Sprint 0 (ce patch, 29/07/2026)
- ✅ Acte officiel de fin signé (ce document).
- ✅ `docs/PLAN_DIRECTEUR_AMELIORATIONS.md` met Jinja2 en statut `décommissionné`.
- ✅ README mis à jour pour pointer vers Next.js comme frontend officiel.

### Étape 2 — Sprint 5 (Semaines 11-12)
- Migration des **6 pages critiques** vers Next.js :
  - Dashboard, Bots (kanban), Backtest, Optimizer, Portfolio, Config
- Tests E2E Playwright sur les 6 pages migrées.
- Les routes HTML Jinja2 correspondantes restent disponibles en
  **cohabitation** (le temps de valider la parité).

### Étape 3 — Sprint 6 (Semaines 13-14)
- Migration des **11 pages secondaires** :
  - Scanner, Replay, Smart Graph, Smart Replay, Compare, Audit, Audit-log,
    Derivatives, Data, ML, Models
- Tests E2E Playwright sur les 17 pages Next.js.
- Audit accessibilité axe-core WCAG 2.1 AA.

### Étape 4 — Suppression physique (fin Sprint 6)
- ⚠ Suppression de `app/web/templates/` (15+ fichiers, ~10 600 lignes).
- ⚠ Suppression de `_tpl()` helpers dans `app/api/main.py`.
- ⚠ Suppression de `app/api/main.py` des routes `@app.get("/", ...)` etc.
- ⚠ Remplacement par des redirects 308 vers le frontend Next.js (port 3000
  ou proxy nginx).
- ⚠ Suppression de `jinja2` du `requirements.txt`.
- ⚠ Suppression de `app/web/static/` (JS/CSS partagés des templates).

### Étape 5 — Sprint 7+ (production)
- Configuration nginx pour servir le build Next.js statique + proxy
  `/api/*` vers FastAPI.
- Documentation déploiement dans `DEPLOY.md` mise à jour.

## Routes API impactées

Les routes **REST** (`/api/*`) ne sont **pas impactées** — elles sont
indépendantes du frontend. Seules les routes **HTML** sont supprimées :

| Route HTML Jinja2 (à supprimer) | Équivalent Next.js (nouveau) |
|---|---|
| `GET /` | `frontend/src/app/portfolio/page.tsx` |
| `GET /backtest` | `frontend/src/app/lab/page.tsx` → `views/backtest-view.tsx` (`?tab=backtest`) |
| `GET /optimizer` | `frontend/src/app/lab/page.tsx` (onglet `?tab=optimizer`) |
| `GET /ml` | `frontend/src/app/lab/page.tsx` (onglet `?tab=ml`) |
| `GET /replay` | `frontend/src/app/lab/page.tsx` (onglet `?tab=replay`) |
| `GET /compare` | `frontend/src/app/lab/page.tsx` (onglet `?tab=compare`) |
| `GET /scanner` | `frontend/src/app/market/page.tsx` (onglet `?tab=scanner`) |
| `GET /smartgraph` | `frontend/src/app/market/page.tsx` (onglet `?tab=smartgraph`) |
| `GET /smartreplay` | `frontend/src/app/market/page.tsx` (onglet `?tab=smartreplay`) |
| `GET /derivatives` | `frontend/src/app/market/page.tsx` (onglet `?tab=derivatives`) |
| `GET /config` | `frontend/src/app/settings/page.tsx` (onglet `?tab=capital`) |
| `GET /settings` | `frontend/src/app/settings/page.tsx` (onglet `?tab=capital`) |
| `GET /portfolio` | `frontend/src/app/portfolio/page.tsx` |
| `GET /bots` | `frontend/src/app/bots/page.tsx` |
| `GET /audit` | `frontend/src/app/audit/page.tsx` |
| `GET /audit-log` | `frontend/src/app/audit-log/page.tsx` |
| `GET /trades` | `frontend/src/app/trades/page.tsx` |
| `GET /models` | `frontend/src/app/models/page.tsx` |
| `GET /data` | `frontend/src/app/data/page.tsx` |

> **Mise à jour S10 et lots de fusion.** Le tableau ci-dessus a été aligné sur
> l'état réel du code. Les 14 premières routes n'ont plus de page dédiée : leur
> contenu a été porté dans les 5 pages méta (`portfolio`, `bots`, `lab`,
> `market`, `settings`) et les anciens fichiers `app/scanner/page.tsx`,
> `app/config/page.tsx`, `app/smartgraph/page.tsx`… ont été supprimés. Les 14
> chemins sont désormais des **redirections 308** déclarées dans
> `frontend/next.config.mjs`, et non plus des 307 comme le prévoyait le plan
> initial de cette note.
>
> **Mise à jour S11.** Les pages méta ont perdu le suffixe `-v2` (commit
> `60b9984`) : les chemins canoniques sont `frontend/src/app/{portfolio,bots,
> settings}/page.tsx`. Les URLs `-v2` restent servies en 308 vers la page sans
> suffixe. Le tableau ci-dessus cite désormais les chemins réels.
>
> **Orphelin trouvé 07/08/2026 — le cookie d'authentification.** La note
> ci-dessus dit la migration « complète » ; elle ne l'était pas tout à fait.
> `_tpl()` ne faisait pas que rendre des templates : il posait le cookie
> HttpOnly `api_key`, seul credential qu'un `new WebSocket()` puisse
> transporter. Sa suppression a laissé `set_cookie` absent de tout `app/`, donc
> le WebSocket temps réel refusé par un 403 muet dès que `web.api_key` est
> renseigné. Le REST n'a rien vu passer : il transite par le proxy Next, qui
> injecte `X-API-Key` côté serveur. Le cookie est désormais reposé par ce même
> proxy, et le WS ramené en same-origin. Cf. CHANGELOG, « Le temps réel
> remarche ».
>
> Second orphelin, non résolu : l'éditeur de paramètres par stratégie de
> `config.html` n'a jamais été reporté. `POST /api/config/strategy-params`
> répond toujours, mais aucune page ne l'appelle.

> **Mise à jour 08/2026.** Le Laboratoire compte un 6ᵉ onglet, `?tab=batch`
> (*Multi-TF*) : il n'a pas d'ancêtre Jinja2, c'est l'ancien `replay-view`
> batch multi-TF déplacé là quand `?tab=replay` est devenu le replay
> interactif bougie-par-bougie (cf. `docs/SPECS_JINJA2_VS_NEXT_MARCHE_LAB.md`
> §8).
>
> Les 5 dernières routes (`/audit`, `/audit-log`, `/trades`, `/models`,
> `/data`) restent de vraies pages Next et ne sont pas redirigées.
>
> Côté FastAPI, `HTML_ROUTES_TO_REDIRECT` (`app/api/main.py`) vise directement
> ces cibles finales pour éviter un double 308 ;
> `tests/test_legacy_redirects.py` vérifie que les deux tables ne divergent
> pas. Détail dans `docs/audit-ui-ux-bot-crypto.md` §Bascule S10.

## Impact dépendances

Avant :
```python
# requirements.txt
jinja2==3.1.6  # utilisé pour app/web/templates
fastapi==0.115.0  # utilise Jinja2Templates
```

Après suppression complète :
```python
# requirements.txt
# jinja2 supprimé (plus aucun template)
fastapi==0.115.0  # reste pour /api/* (REST, pas de templates)
```

⚠ `fastapi` lui-même dépend de `jinja2` optionnellement — il ne sera pas
supprimé du pip, mais ne sera plus importé par notre code. On garde la
dépendance transitive pour ne pas casser FastAPI docs endpoints.

## Impact tests

- ✅ Tests API REST (`/api/*`) — **non impactés**.
- ⚠ Tests UI basés sur TestClient + parsing HTML Jinja2 — **à supprimer**
  ou migrer vers Playwright E2E Next.js.
- ✅ Tests backend (risk, exchange, backtest, etc.) — **non impactés**.

Voir `tests/test_vizion.py`, `tests/test_api_routes.py` pour les tests
HTML à migrer.

## Impact utilisateurs

- **Utilisateurs locaux** : URL d'accès passe de `:8000/` à `:3000/`
  (ou nginx proxifie les deux). Documentation à mettre à jour.
- **Bookmarks** : redirect 307 assure la transition sans casse.
- **API consommateurs** : aucun changement (`/api/*` inchangé).

## Voir aussi

- `docs/PLAN_DIRECTEUR_AMELIORATIONS.md` — Décision D4, Sprints 5-6
- `docs/audit-externe/AUDIT_TECHNIQUE_BOT_CRYPTO_V12.md` — §5 UI/UX
- `docs/audit/06-ui-ux.md` — Audit interne UI/UX (archive)
- `frontend/README.md` — Documentation frontend Next.js
- `frontend/src/components/layout/sidebar.tsx` — Navigation Next.js

---

**Signé** : Audit externe Z.ai — 29 juillet 2026.
**Approuvé par** : Équipe projet bot-crypto.
**Date de suppression physique** : Fin Sprint 6 (semaine 14).
