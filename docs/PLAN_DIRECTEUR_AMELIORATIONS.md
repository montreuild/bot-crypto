# Plan directeur d'amélioration — Bot-Crypto V12

> **Document de référence unique** pour le plan d'amélioration issu de l'audit
> technique externe du 29 juillet 2026. Remplace les bribes de roadmap
> historiques dispersées dans `docs/SYNTHESE_VISION_PRODUIT.md` (vision produit)
> et `docs/audit/` (audits internes partiels).
>
> **Source** : `docs/audit-externe/AUDIT_TECHNIQUE_BOT_CRYPTO_V12.md` et `.pdf`
> (audit indépendant en deux passes V1/V2 + autocritique comparative).
>
> **Statut** : `accepted` — adopté comme feuille de route unique le 29/07/2026.
> Toute évolution doit venir amender ce document (pas en créer un nouveau).

---

## Synthèse exécutive

L'audit externe a noté le projet **3.4/5 (V2)** — mature mais non
production-ready sans exécuter le Sprint 0. Trois risques critiques convergents
(V1 hors docs + V2 avec docs) ont été identifiés :

1. **🔴 Sur-risque sizing live** — `risk.compute_size` divise par l'ATR brut
   alors que le stop est à `mult × ATR`. Risque réel = 2,5× le risque affiché.
2. **🔴 Bypass auth via `X-Forwarded-For`** — spoofing trivial de l'IP cliente
   quand `host: 0.0.0.0` sans `api_key`.
3. **🟠 Parité backtest↔live incomplète** — ne couvre que les formules monétaires
   (le test de parité existe mais ignore le sizing et le timing).

Le plan résout ces risques + la dette technique structurelle (40 stratégies
dont 17 variantes Opus copiées-collées) + la migration Jinja2 → Next.js + la
conformité réglementaire (MiCA/AMF/SEC).

**Format** : 8 sprints × 2 semaines = 16 semaines (4 mois).
**Capacité** : 1 dev senior ou 2 devs mid.
**Total** : 173 story points (1 SP ≈ 1 jour-homme).

---

## Décisions structurelles actées par ce plan

| # | Décision | Justification | Sprint |
|---|---|---|---|
| D1 | **Python 3.14 obligatoire** (au lieu de 3.12) | Finalisation 3.14 stable Oct 2025, perf +40% sur hot loops, meilleur GIL, support pattern matching étendu. Pré-requis pour LightGBM 4.6+ et Optuna 4.2. | S0 |
| D2 | **`setup.sh` cross-plateforme** (Linux/macOS/Windows Git Bash + WSL) | 80% des utilisateurs Windows utilisent Git Bash ou WSL — un seul script réduit la maintenance. | S0 |
| D3 | **Guide de démarrage Windows dédié** (`docs/DEMARRAGE_WINDOWS.md`) | Le README actuel est Ubuntu-first. Le guide Windows couvre Git Bash, WSL2, Python 3.14 install, venv, OKX paper. | S0 |
| D4 | **Fin officielle de Jinja2** (`docs/FIN_JINJA2.md`) | Le frontend Next.js est désormais le frontend officiel. Les templates Jinja2 sont décommissionnés et supprimés. Date de fin : fin Sprint 6. | S0 → S6 |
| D5 | **Mode `performance` retenu** pour l'allocation** (S4-05) | Le mode `continuous` n'a jamais été activé en production. Trancher en faveur de `performance` réduit la dette de code dormant. | S4 |
| D6 | **Lifecycle automatique avec override manuel** (S4-07) | ✅ **Appliquée (S11)** : les 15 slots forcés sont retirés de `config.yaml`, la clé est renommée `lifecycle.force_active` (l'ancien nom `manual_active` reste lu, déprécié). Le défaut est `[]` : la machinerie candidat/essai/actif/retiré décide seule. | S4 → S11 |
| D7 | **Migration Jinja2 → Next.js par ordre de criticité** (S5 → S6) | 6 pages critiques d'abord (Dashboard, Bots, Backtest, Optimizer, Portfolio, Config), puis 11 pages secondaires. Suppression finale des templates après validation E2E. | S5-S6 |

---

## Roadmap consolidée

```
Phase 1 — Survie (S0)            🔴  21 SP    Risque -60%
Phase 2 — Fondations (S1-S2)     🟠  66 SP    Risque -75%
Phase 3 — Trading (S3-S4)       🔵  59 SP    Risque -90%
Phase 4 — Produit (S5-S6)       🟢  79 SP    Risque -95%
Phase 5 — Industrialisation (S7) 🟣  40 SP   Risque -98%
                                Total: 265 SP
```

### Sprint 0 — Stabilisation critiques (21 SP)

| ID | Tâche | SP | Sévérité | Statut |
|---|---|---|---|---|
| S0-01 | Corriger sizing live (`risk.compute_size` divise par `mult×ATR`, pas ATR brut) | 3 | Critique | ✅ Fait dans ce patch |
| S0-02 | Valider `X-Forwarded-For` (TRUSTED_PROXIES) | 2 | Critique | ✅ Fait dans ce patch |
| S0-03 | Brancher le rate-limiter SlowAPIMiddleware | 1 | Élevée | ✅ Fait dans ce patch |
| S0-04 | Refuser démarrage live si `host=0.0.0.0` sans `api_key` | 1 | Élevée | ✅ Fait dans ce patch |
| S0-05 | Élaguer la bougie en cours côté live avant scoring | 2 | Élevée | ✅ Fait dans ce patch |
| S0-06 | Corriger XSS UI-01 (`data.html` `esc()` n'échappe pas guillemets) | 2 | Élevée | ✅ Fait dans ce patch |
| S0-07 | Ajouter `pip-audit` en CI | 1 | Moyenne | ✅ Fait dans ce patch |
| S0-08 | Documenter Go/No-Go checklist | 2 | Moyenne | ✅ Fait dans ce patch |
| S0-09 | Télémétrie minimale (alertes Telegram critiques) | 3 | Moyenne | ✅ Fait dans ce patch |
| S0-10 | Tests E2E sizing/auth/rate-limit | 3 | Moyenne | ✅ Fait dans ce patch |
| S0-11 | Supprimer `allow_insecure: true` de la config par défaut | 1 | Faible | ✅ Fait dans ce patch |

### Sprint 1 — Tests & Observabilité (34 SP, non couvert ici)

Voir `docs/audit-externe/AUDIT_TECHNIQUE_BOT_CRYPTO_V12.md` § Sprint 1.

### Sprint 2 — Refactor Architecture (32 SP, sélection)

| ID | Tâche | SP | Statut |
|---|---|---|---|
| S2-01 | Factoriser la famille Opus autour d'une `OpusBase` | 8 | ⏳ Reporté (chantier lourd, hors scope ce patch) |
| S2-02 | Décider stratégies "production" + archiver le reste | 3 | ⏳ Reporté |
| S2-03 | Ajouter `status:` aux YAML stratégies | 2 | ⏳ Reporté |
| S2-04 | Découper `optimizer_search.py` (1033 L) en sous-modules | 3 | ✅ Fait dans ce patch |
| S2-05 | Découper `indicators.py` en sous-modules | 3 | ✅ Fait dans ce patch |
| S2-06 | Découper `live_trader.py` (951 L) | 3 | ✅ Fait dans ce patch |
| S2-07 | Versioning ML modèles (hash features + date) | 3 | ✅ Fait (app/ml/model_versioning.py) |
| S2-08 | Marquer `research/` comme archive | 2 | ⏳ Reporté |
| S2-09 | Audit et nettoyage `models/_archive/` | 2 | ⏳ Reporté |
| S2-10 | Refactor `SignalPipeline` (préserver hints exécution) | 3 | ✅ Fait dans ce patch |

### Sprint 3 — Backtest robuste (31 SP, sélection)

| ID | Tâche | SP | Statut |
|---|---|---|---|
| S3-01 | Optimiser/forward-tester par symbole | 5 | ⏳ Reporté |
| S3-02 | Implémenter le Deflated Sharpe au gate de naissance | 3 | ✅ Fait (`opt_scoring.deflated_sharpe_ratio`) |
| S3-03 | Exiger ≥ 10 trades OOS minimum | 2 | ⏳ Reporté |
| S3-04 | Walk-forward dans la décision d'apply | 3 | ⏳ Reporté |
| S3-05 | Cône d'edge + contrat Monte-Carlo glissant | 5 | ✅ Déjà fait (app/core/oos_tracker.py) |
| S3-06 | Aligner sémantique portefeuille backtest↔live | 3 | ⏳ Reporté |
| S3-07 | Ajouter Sortino, Calmar, alpha vs Buy & Hold | 2 | ✅ Fait (app/core/performance_metrics.py) |
| S3-08 | Corriger `edge_lookback_days: 365` tronqué silencieusement | 1 | ✅ Fait (warning explicite dans forward_test.py) |
| S3-09 | Stress tests par régimes (bull/bear/range) | 3 | ✅ Fait (app/engine/regime_stress_test.py) |
| S3-10 | Détecter overfitting ML (AUC < 0.55 → warning) | 2 | ✅ Fait (app/ml/overfitting_gate.py) |
| S3-11 | Réduire timeout ML + libérer `_ml_lock` proprement | 2 | ✅ Fait (app/ml/trainer.py — _retrain_with_timeout) |

### Sprint 4 — Risk Management (28 SP, sélection)

| ID | Tâche | SP | Statut |
|---|---|---|---|
| S4-01 | Verrou sur `CapitalAllocator` | 2 | ❌ Sans objet (S12) — `CapitalAllocator` supprimé en `e0306c2`, remplacé par `RiskLedger` dont `reserve`/`release` sont atomiques sous `RLock` |
| S4-02 | Transaction atomique `save_trade` + `update_daily_stats` | 2 | ⏳ Reporté |
| S4-03 | Persister stats hebdo allocator en DB | 3 | ❌ Sans objet (S12) — était fait dans `capital_allocator.py` `_persist_weekly_stats` ; il n'y a plus de budget hebdo à rééquilibrer sous `RiskLedger` |
| S4-04 | Vraie mesure de corrélation (matrice rendements) | 3 | ✅ Fait (app/core/correlation_matrix.py) |
| S4-05 | Trancher allocation — mode `performance` retenu (D5) | 3 | ✅ Fait (décision actée) |
| S4-06 | Clarifier lifecycle ↔ budgets (cohérence `force_active` ↔ `slot_budgets`) | 2 | ✅ Fait (slot_lifecycle.py warnings) |
| S4-07 | Activer lifecycle automatique + override manuel possible (D6) | 5 | ✅ **Fait (S11)** — liste vidée, clé renommée `force_active`, `set_force_active()` + alias déprécié |
| S4-08 | Circuit-breaker réseau global (halt après ~10 min) | 2 | ⏳ Reporté |
| S4-09 | Slippage paper proportionnel à la taille | 2 | ⏳ Reporté |
| S4-10 | Timeout scoring pipeline configurable | 1 | ⏳ Reporté |
| S4-11 | Renseigner `entry_time` en DB | 1 | ✅ Fait (database.py save_trade) |
| S4-12 | Cap budget slot +5% agrégé | 2 | ✅ Fait (capital_allocator.py can_allocate) |

### Sprint 5 — Migration Next.js (44 SP, sélection)

| ID | Tâche | SP | Statut |
|---|---|---|---|
| S5-01 | Corriger UI-02 (config.html mono-symbole) dans Next.js | 5 | ✅ Fait (config/page.tsx avec sélecteur symbole) |
| S5-02 | Corriger UI-03 (audit.html écrase OOS) dans Next.js | 3 | ✅ Déjà fait (audit/page.tsx gère slot_key 3-parties) |
| S5-03 | Corriger UI-04 (trades.html filtre Slot) dans Next.js | 2 | ✅ Fait (trades/page.tsx filtre slot 3-parties) |
| S5-04 | Migration page Dashboard Next.js | 5 | ✅ Déjà fait |
| S5-05 | Migration page Bots Next.js (kanban) | 5 | ✅ Déjà fait |
| S5-06 | Migration page Backtest Next.js | 5 | ✅ Déjà fait |
| S5-07 | Migration page Optimizer Next.js | 5 | ✅ Déjà fait |
| S5-08 | Migration page Portfolio Next.js | 3 | ✅ Déjà fait |
| S5-09 | Migration page Config Next.js | 3 | ✅ Fait (page réécrite avec multi-symbole) |
| S5-10 | WebSocket provider Next.js | 3 | ✅ Déjà fait (ws-provider.tsx) |
| S5-11 | Étiqueter fenêtres de métriques | 2 | ✅ Déjà fait (métriques par fenêtre) |
| S5-12 | i18n FR/EN | 3 | ✅ Déjà fait (i18n.tsx) |

### Sprint 6 — UI/UX Design System & Accessibilité (35 SP)

| ID | Tâche | SP | Statut |
|---|---|---|---|
| S6-01 | Design system formalisé (Storybook) | 5 | 🟡 Partiel (DESIGN_SYSTEM.md tokens, Storybook reporté) |
| S6-02 | Audit accessibilité axe-core WCAG 2.1 AA | 3 | 🟡 Partiel — **règles écrites, jamais outillées** (voir S6-11) |
| S6-03 | Migration pages secondaires (11) Next.js | 8 | ✅ Déjà fait (23 pages Next.js build OK) |
| S6-04 | Responsive mobile | 3 | ✅ Déjà fait (Tailwind responsive) |
| S6-05 | Performance perçue (optimistic UI, skeletons) | 3 | ✅ Déjà fait (skeletons dans les pages) |
| S6-06 | Notifications UI 3 niveaux | 2 | ✅ Déjà fait (sonner + 3 niveaux) |
| S6-07 | Onboarding utilisateur | 3 | ⏳ Reporté |
| S6-08 | Documentation utilisateur | 3 | ✅ Fait (DEMARRAGE_WINDOWS.md, FIN_JINJA2.md) |
| S6-09 | Déprécier Jinja2 formellement → **SUPPRIMÉ physiquement** | 2 | ✅ Fait (templates + routes supprimés, redirects 308) |
| S6-10 | Analytics produit (PostHog opt-in) | 3 | ⏳ Reporté (PSAN sensible) |
| S6-11 | Outiller réellement l'accessibilité (`@axe-core/playwright` sur les 20 routes) | 3 | ⏳ À faire — `axe-core` n'est dans aucun `package.json` (cf. S6-02) |
| S6-12 | États d'erreur des pages (spinner infini quand l'API est injoignable) | 3 | ✅ Fait (`components/ui/query-state.tsx`, bandeau global, timeout `apiFetch`) |
| S6-13 | Jouer les tests E2E Playwright en CI (job `e2e` dans `ci.yml`) | 3 | ✅ Fait — débloqué par S6-12, 20/20 verts backend éteint |
| S6-14 | Le lint frontend n'a jamais tourné : aucune config ESLint dans `frontend/` | 2 | ✅ Fait — `.eslintrc.json` + 22 erreurs et 3 avertissements corrigés, `npm run lint` vert |
| S6-15 | Auth du frontend Next.js : proxy same-origin injectant `X-API-Key` côté serveur | 5 | ✅ Fait (`src/app/api/[...path]/route.ts`) — supprime aussi tout CORS |
| S6-16 | `.env` généré par setup.sh mais jamais lu par l'application | 2 | ✅ Fait (`_ensure_dotenv` dans `load_config`) |
| S6-17 | 6 tests cassés sur `main`, dont 2 vacants sur le correctif critique S0-01 | 3 | ✅ Fait — suite 1380/1380 |

### Sprint 7 — Production & Conformité (40 SP, non couvert)

Voir `docs/audit-externe/AUDIT_TECHNIQUE_BOT_CRYPTO_V12.md` § Sprint 7.
Reporté à une itération suivante.

---

## Décisions techniques importantes

### Python 3.14 (D1)

**Pourquoi 3.14 pas 3.13 ?**
- 3.13 a introduit le free-threaded mode expérimental mais instable pour
  certains C extensions (LightGBM, Optuna).
- 3.14 (sortie Oct 2025) stabilise le free-threaded mode + apporte
  +40% perf sur les hot loops (backtest, indicators_precompute).
- `polars==1.0.0` et `lightgbm==4.4.0` testés OK sur 3.14.
- `ccxt==4.5.68` testé OK sur 3.14.

**Pinning** : `requirements.txt` épinglera les versions testées sur 3.14.

### Migration Jinja2 → Next.js (D4)

**Décision** : le frontend Next.js (`frontend/`) devient le **frontend
officiel unique**. Les templates Jinja2 (`app/web/templates/`) sont
**supprimés** à la fin du Sprint 6 après validation E2E.

**Plan de suppression** :
1. Sprint 5 : migration des 6 pages critiques en Next.js (avec parité
   fonctionnelle).
2. Sprint 6 : migration des 11 pages secondaires.
3. Validation E2E Playwright sur les 17 pages Next.js.
4. Suppression physique de `app/web/templates/` + `_tpl()` helpers dans
   `app/api/main.py`.
5. Routes HTML de `main.py` renommées en redirects 308 vers le frontend
   Next.js (port 3000 ou proxy nginx).

**Voir** : `docs/FIN_JINJA2.md` pour l'acte officiel de fin.

#### Vérification post-migration (29/07/2026, commit `0101fe9`)

Audit de complétude mené après S6-09. **La migration est complète** — aucune
page, route ou ressource Jinja2 n'a été oubliée :

| Contrôle | Résultat |
|---|---|
| `app/web/templates/` et `app/web/static/` supprimés | ✅ `app/web/` ne contient plus que `__init__.py` |
| Parité des pages | ✅ 19 templates supprimés (dont `base.html`) → **18 pages, 18 routes Next.js**, 0 orphelin |
| Imports `Jinja2Templates` / `StaticFiles` / `HTMLResponse` | ✅ Aucun — seuls des commentaires historiques subsistent |
| Redirects `HTML_ROUTES_TO_REDIRECT` | ✅ Exhaustifs vs. les 18 anciennes routes + `/slots` (l'ancien `/` servait le dashboard ; `/dashboard` n'a **jamais** existé côté FastAPI) |
| `jinja2==3.1.6` dans `requirements.txt` | ✅ Volontaire — transitive FastAPI (`/api/docs`), épinglée pour SEC-010 |
| Tests Python référençant du HTML Jinja2 | ✅ Aucun ; `tests/test_api_routes.py` + `test_vizion.py` → **22 passed** |
| `npm run type-check` | ✅ 0 erreur |
| `npm run build` | ✅ 23/23 pages statiques générées |

**Écarts trouvés** (aucun ne remet en cause la migration, tous tracés ci-dessus) :

1. **`/models` n'était pas couvert par les tests E2E** alors que c'est une page
   migrée depuis `models.html` → corrigé dans `frontend/e2e/tests/pages.spec.ts`.
2. **`/dashboard`, `/bots`, `/config` bouclaient sur un spinner infini quand
   l'API est injoignable** (`if (isLoading || !data)` : sur erreur réseau
   `isLoading` repasse à `false` mais `data` reste `undefined`). Ni titre, ni
   message d'erreur, ni bouton de réessai. Les pages Jinja2, rendues côté
   serveur, n'avaient pas ce mode de défaillance → **S6-12, corrigé**.

   Deuxième cause, plus vicieuse : **`fetch` n'avait aucune échéance**. Selon la
   configuration réseau, un backend éteint refuse le SYN (échec immédiat) ou le
   laisse filtrer — observé en dev Windows, connexions bloquées en `SYN_SENT`.
   La requête n'était alors *jamais* résolue : react-query restait `pending`,
   `error` jamais peuplé, et même un état d'erreur correct n'aurait rien
   affiché. `apiFetch` borne désormais chaque requête (15 s par défaut,
   `timeoutMs: 0` pour les traitements longs : backtest, replay, forward-test,
   refetch OHLCV, fast-analysis).
3. **Les tests E2E ne tournaient dans aucun job CI** — `ci.yml` couvrait ruff,
   pytest, pip-audit, lint/type-check/build frontend, mais pas Playwright
   → **S6-13, corrigé** (débloqué par S6-12 : 20/20 verts backend éteint).

   La suite complète comptait par ailleurs **5 tests qui n'étaient jamais
   passés** : `Meta+K` ne fonctionne que sur macOS, et trois locators
   violaient le strict mode en résolvant plusieurs éléments (le libellé
   figure aussi dans la nav latérale). Corrigés → 32/32.

4. **Le frontend ne pouvait pas s'authentifier** → **S6-15**. Dès que
   `web.api_key` est renseigné, `verify_api_key` exige la clé ; or plus rien
   ne posait le cookie HttpOnly depuis la suppression de `_tpl()` avec les
   templates. Toutes les routes protégées répondaient 403. `next.config.mjs`
   déclarait pourtant un proxy `/api/:path*` « pour éviter les problèmes
   CORS », mais `api.ts` le contournait en tapant `NEXT_PUBLIC_API_URL` en
   absolu depuis le navigateur : le proxy existait, personne ne l'utilisait.
   Remplacé par un route handler qui injecte `X-API-Key` côté serveur — la
   clé ne transite jamais par le bundle, et les appels deviennent
   same-origin, donc sans CORS ni whitelist d'origines à maintenir.

   Au passage : la whitelist CORS ne contenait pas le port 3000 (elle datait
   de l'ère Jinja2, où l'UI était servie par FastAPI sur 8000/8001).
4. **`axe-core` n'est installé nulle part** : S6-02 documentait les règles WCAG
   dans `DESIGN_SYSTEM.md` sans jamais les outiller → **S6-11**.

### Mode allocation (D5)

**Décision** : le mode `continuous` (calculé mais jamais appliqué) est
**supprimé**. Le mode `performance` (rebalance hebdo) devient le défaut
unique. La complexité est réduite, le code dormant éliminé.

### Lifecycle automatique (D6)

**Décision** : `lifecycle.manual_active` (15 slots forcés) est retiré
de la config par défaut. La machinerie candidat/essai/actif/retiré
décide seule. Un override manuel reste possible via `lifecycle.force_active:
[strategy::tf::symbol]` pour tests/debug.

**✅ Appliquée en S11.** Trois précisions relevées à l'implémentation, qui ne
figuraient dans aucun des audits :

1. **La liste ne décidait pas quels bots tradent.** La sélection vient du
   classement OOS (`optimizer_results` + seuil `MIN_VIABLE_SCORE` +
   `trading.top_strategies_per_tf`, cf.
   `app/engine/opt_persistence.py::get_active_strategies_per_tf`). Les audits
   V12 et `ANALYSE_CRITIQUE` en concluaient un risque de « trader des setups
   non validés OOS » : c'est inexact, le classement OOS s'appliquait déjà.
2. **Le forçage bloquait aussi le RETRAIT.** Le bypass court-circuitait les
   deux règles de sortie (budget effondré, live qui contredit la simulation en
   perdant) : un slot forcé n'était jamais retiré, même perdant, et n'entrait
   donc jamais dans la file de ré-optimisation. C'est le vrai coût du forçage,
   désormais verrouillé par `test_force_active_also_blocks_the_retrait`.
3. **Les 15 clés étaient au format hérité 2-parties** (`strategy::tf`), donc
   appliquées par préfixe à **tous les symboles**.

Incohérence `manual_active` (15) ↔ `slot_budgets` : les audits annonçaient
7 slots budgétés, le fichier n'en portait qu'**un** (`trend_rider::1h::BTC/USDC`),
lui-même absent de `manual_active`. Le retrait de la liste dissout la question.

---

## Comment contribuer à ce plan

1. Toute modification du plan doit **amender ce document** (pas en créer un
   nouveau).
2. Statuts : `⏳ Reporté` / `🟡 Partiel` / `✅ Fait` / `❌ Annulé`.
3. Toute annulation doit être justifiée dans le tableau de décision D1-D7.
4. Ce document est la **source unique de vérité** pour le plan d'amélioration.
   Les autres docs de `docs/` peuvent référencer mais pas dupliquer.

---

## Références

- `docs/audit-externe/AUDIT_TECHNIQUE_BOT_CRYPTO_V12.md` — audit source
- `docs/audit-externe/AUDIT_TECHNIQUE_BOT_CRYPTO_V12.pdf` — version PDF
- `docs/audit-externe/diagrams/` — 3 diagrammes (architecture, flux, roadmap)
- `docs/SYNTHESE_VISION_PRODUIT.md` — vision produit (toujours valable)
- `docs/VISION_CIBLE_BOTS_AUTONOMES.md` — vision cible (toujours valable)
- `docs/audit/` — audits internes historiques (archivés)
- `docs/FIN_JINJA2.md` — acte officiel de fin de Jinja2
- `docs/DEMARRAGE_WINDOWS.md` — guide de démarrage Windows
