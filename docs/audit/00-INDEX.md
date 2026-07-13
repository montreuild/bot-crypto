# 🔍 Audit d'amélioration post-refonte — Index & plan d'exécution

Audit exhaustif du bot réalisé le **2026-07-11**, juste après la refonte
« configs par symbole » (slots `strategy::tf::symbol`, PR #151). Sept domaines
analysés par des agents indépendants en lecture seule. **89 items** au total,
chacun rédigé pour être **directement exécutable par un nouvel agent** sans
autre contexte (fichiers:lignes, directive pas-à-pas, critère d'acceptation).

> ⚠ **Ce document est un PLAN : aucune des recommandations n'a été appliquée.**
> Chaque item doit être validé avant exécution.

## Les fichiers

| Fichier | Domaine | Items | P1 |
|---|---|---|---|
| [01-architecture.md](01-architecture.md) | Couches, couplage, fichiers-dieux, duplication de constantes | 14 | 3 |
| [02-code-mort.md](02-code-mort.md) | Stratégies mortes, pyflakes, caches versionnés | 9 | 2 |
| [03-smc-ict-indicateurs.md](03-smc-ict-indicateurs.md) | Détecteurs ICT non câblés, manques canon, perfs moteur | 15 | 3 |
| [04-backtest-optimiseur.md](04-backtest-optimiseur.md) | Réalisme, Monte-Carlo, garde-fous d'apply, IS/OOS | 13 | 6 |
| [05-live-ops-securite.md](05-live-ops-securite.md) | Auth, watchdog, persistance, threads, migrations | 14 | 4 |
| [06-ui-ux.md](06-ui-ux.md) | XSS, écrans mono-symbole, duplication JS, accessibilité | 12 | 5 |
| [07-tests-ci-docs.md](07-tests-ci-docs.md) | CI absente, couverture, lint, CHANGELOG | 12 | 3 |

## Règles transverses (discipline du repo — s'appliquent à TOUT item)

1. **Byte-identique quand off** : tout nouveau flag/détecteur est off par défaut ;
   l'empreinte de régression du backtest (BTC 4h) doit être inchangée.
2. **Mesurer avant d'activer** : vrai Backtester, split IS 2/3 / OOS 2024+,
   rapporter PF/PnL/Sharpe/n — jamais d'activation sur une intuition.
3. **`pytest -q` vert** après chaque item ; commit atomique par item avec le
   tag de l'item dans le message (ex. `[BT-01] …`).
4. **Ne pas committer `data/`** (`git add -A ':!data'`) sauf décision DEAD-03.

## 🚨 Vague 0 — Régressions de la refonte per-symbole — ✅ RÉALISÉE (2026-07-11)

Ces items sont des conséquences directes de la refonte : le cœur est migré,
mais des chemins secondaires supposaient encore l'ancien slot 2-parties.

| Item | Quoi | Effort | État |
|---|---|---|---|
| **BT-01** | `/api/optimize/apply` n'envoie pas le symbole → **écrase les configs des autres symboles** | S | ✅ |
| **OPS-01** | `manual_active` (15 clés) et `slot_budgets` (7 clés) hérités 2-parties **orphelins silencieux** | M | ✅ |
| **UI-03** | audit.html écrase les résultats OOS entre symboles (+ backtest_history.py en 2-parties) | M | ✅ |
| **UI-04** | trades.html : filtre « Slot » 2-parties incohérent | S | ✅ |
| **BT-12** | Route `/api/optimize/start` mono-symbole (seul le scheduler boucle) | S | ✅ |
| **UI-02** | config.html : édition des params sans dimension symbole | L | ✅ |

## 🔴 Vague 1 — Sécurité & intégrité — ✅ RÉALISÉE (2026-07-11)

| Item | Quoi | Effort |
|---|---|---|
| OPS-02 | host 0.0.0.0 sans api_key = warning non bloquant | S |
| UI-01 | XSS via `esc()` incomplet dans data.html | S |
| OPS-03 | Watchdog dead-man jamais lancé (aucun unit systemd) | S |
| OPS-04 | Aucune notification externe activée (HALT silencieux) | S |
| OPS-05 | 5 endpoints GET sans verify_api_key | S |
| OPS-07 | Écriture parquet non atomique + verrou intra-process seulement | M |

## 🟠 Vague 2 — Intégrité de la mesure — ✅ RÉALISÉE (2026-07-11)

| Item | Quoi | Effort |
|---|---|---|
| BT-02 | Monte-Carlo dégénéré (permutation → p5=p95, prob_profit binaire) | S |
| BT-03 | max_notional_pct 0.50 backtest vs 0.20 live (tailles ×2,5) | S |
| BT-04 | Apply manuel sans garde-fou qualité (409 si < baseline) | M |
| BT-06 | Seuils de significativité incohérents (2/3/10 trades) | S |
| BT-08 | Conventions IS/OOS unifiées (app/core/is_oos.py) | M |
| ARCH-01 | `_merge_params` live ne filtre pas `_GLOBAL_PARAM_KEYS` (parité live/backtest) | M |
| BT-07 | Walk-forward branché sur l'auto-apply | M |
| BT-09 | Réduction de risque en drawdown absente du backtest | M |

## 🟡 Vague 3 — Nettoyage & outillage

| Item | Quoi | Effort |
|---|---|---|
| DEAD-01 | Supprimer 8 stratégies mortes (7 426 lignes) — garder v8/v10/pretrained_v4 | M |
| DEAD-02, DEAD-05 | scoring v3 morte ; fix pyflakes opus_omnibus_v11 (actif) | S |
| TEST-01 | CI GitHub Actions (pytest + lint) | S |
| TEST-04/05 | Config lint (ruff recommandé) puis DEAD-07 (67 imports morts) | M |
| DEAD-03 | ⚠ décision utilisateur : parquets versionnés (choix actuel volontaire) + sort de XRP_USDC | S |
| TEST-06 | Tests lents/fragiles : fixtures synthétiques + marker slow | M |

## 🟢 Vague 4 — Architecture ✅ RÉALISÉE (2026-07-12)

Ordre interne : ARCH-02 (param_resolution → core) débloque ARCH-03/11 ;
ARCH-08 + DEAD-04 fusionnent en un seul module `app/core/timeframes.py`.

ARCH-02 → ARCH-08+DEAD-04 → ARCH-03 → ARCH-05 (+_pos_key) → ARCH-04 → ARCH-09
→ ARCH-10 → OPS-08/09 (migrations + index DB) → OPS-10 (lock allocateur)
→ ARCH-06/07/14 (découpage fichiers-dieux) → ARCH-12/13.

Réalisation (un commit taggé par item ; couches documentées dans
`ARCHITECTURE.md › Couches et règles de dépendance`) :

- ✅ ARCH-08+DEAD-04 : `app/core/timeframes.py` (TF_SECONDS/TF_MINUTES/TF_MS/HTF_MAP uniques)
- ✅ ARCH-02 : `app/core/param_resolution.py` (fin de l'inversion engine→live)
- ✅ ARCH-05 : `build_slot_key`/`build_pos_key` canoniques + 4 reliquats 2-parties corrigés
- ✅ ARCH-04 : live_trader n'importe plus app.api (`core/yaml_io.update_config_yaml`, verrou unique)
- ✅ ARCH-09 : forward-test déplacé core→engine (`app/engine/forward_test.py`)
- ✅ ARCH-10+11 : `DEFAULT_TAKER_FEE`/`DEFAULT_MAKER_FEE` + `DEFAULT_CONFIG_SYMBOL` partout
- ✅ OPS-08+09 : `_migrate_schema` idempotente + index `ix_trades_strategy_tf_time`
- ✅ OPS-10 : RLock interne au CapitalAllocator + accesseurs verrouillés
- ✅ ARCH-13 : `DATA_ROOT` + `lazy_singleton` factorisé
- ✅ ARCH-06 : live_trader.py 1240→470 lignes (AutoOptMixin + HealthMixin,
  chemin d'ouverture dans PositionMixin)
- ✅ ARCH-07 : routes scanner 995→260 lignes (`app/api/services/scanner_service.py`,
  réponses byte-identiques vérifiées sur fixture)
- ✅ ARCH-14 : smc.py → façade + 5 modules ; `smart_money_signals.py` extrait
  (byte-identique sur fixture)
- ✅ ARCH-03 : déjà résolu par la refonte per-symbole (`_select_symbol_entry`
  partagé par `resolve_strategy_params` ET `get_active_strategies_per_tf`) — vérifié
- ✅ ARCH-12 : partie prioritaire résolue par ARCH-04 (live sans app.api.state) ;
  l'encapsulation AppState complète reste optionnelle (aucune inversion restante)

## 🔵 Vague 5 — Recherche d'edge SMC/ICT ✅ RÉALISÉE (2026-07-12, reliquat SMC-02 clos le 2026-07-13)

Résultats détaillés : `docs/audit/mesures-vague5.md`. Synthèse :

- ✅ SMC-03 liquidité calendaire (off — sweeps OOS BTC 4h légèrement mieux, IS dégradé)
- ✅ SMC-01 SMT à l'origine (off — l'inertie est levée mais le filtre coûte de l'OOS)
- ✅ SMC-11 inducement — **ACTIVÉ BTC 4h** (OOS +68→+75, PF 1.47→1.88, WR +7 pts)
- ✅ SMC-04/05/06/07 judas / TP σ / BPR+CE / silver bullet (off — pas de preuve OOS)
- ✅ SMC-12/13/14 IPDA / mitigation / AMD sessions (off — mit_exclude prometteur
  pour une future calibration ETH)
- ✅ SMC-09/10 famille SMC + grilles dans fast_analysis ; BT-10 slippage taille
  (off — nul au capital actuel, matériel > 1 M)
- ✅ SMC-15 index OB HTF vizion (mémoïsation par bucket, sortie identique)
- ✅ SMC-02 : hoisting des scalaires h[i]/l[i]/c[i]/o[i] hors des boucles de
  cycle de vie (rejections/FVG/OB/breakers dominaient 76 % du coût) —
  ×1.65 à ×1.85 mesuré BTC/ETH 4h/1h, sortie strictement identique
  vérifiée (comparaison profonde vs git HEAD, 12 combinaisons) + test de
  non-régression permanent (`TestAnalyzeSnapshotRegression`). Piste
  alternative testée et rejetée (suppression paresseuse : neutre). Un
  remplacement par index triés/bisect reste possible mais le profil
  résiduel (dominé par les lookups dict Python) rend le gain incertain
  — non engagé (cf. mesures-vague5.md).
- ⛔ BT-11 exclu par décision utilisateur (multi-crypto corrélé assumé).

## 🔵 Vague 5 (référence d'origine) — Recherche d'edge SMC/ICT (mesures, gains potentiels)

Chaque item = flag off + campagne de mesure. Les plus prometteurs d'abord :

1. **SMC-03** Liquidité calendaire PDH/PDL/PWH/PWL (canon le plus utilisé, absent)
2. **SMC-01** SMT à la barre d'origine du setup (lève l'inertie mesurée)
3. **SMC-11** Inducement pour smart_money (déjà validé côté vizion)
4. SMC-04/05/06/07 (judas, std-dev TP, BPR+CE, silver bullet — détecteurs déjà écrits)
5. SMC-12 (IPDA dealing range), SMC-13 (mitigation blocks), SMC-14 (AMD ancré sessions)
6. SMC-09/10 (extensions fast_analysis), BT-10 (slippage dépendant de la taille), BT-11 (plafond groupe corrélé BTC+ETH)
7. SMC-02 (profiling O(n²) du moteur — avant les campagnes massives multi-symboles), SMC-15 (index OB HTF vizion)

## 🟡 Vague 6 — UX, accessibilité, docs ✅ RÉALISÉE (2026-07-13, sauf TEST-11 bloqué par DEAD-01)

UI-05 (static/js partagé) débloque UI-08 et UI-10 → UI-06/07 (a11y)
→ UI-09 (liens data↔scanner) → UI-11 (terminologie) → UI-12 (adopter
showSkeleton — **tranche le conflit avec DEAD-08 : on adopte, on ne supprime pas**)
→ TEST-02/03/07 (tests LiveTrader/API/lifecycle) → TEST-08/10 (docs schéma +
flux live) → TEST-09 (découpage CHANGELOG) → TEST-11 (smoke stratégies, APRÈS
DEAD-01) → TEST-12 (lock requirements).

Détails : `docs/audit/06-ui-ux.md` (UI-0x) et `docs/audit/07-tests-ci-docs.md` (TEST-0x). Synthèse :

- ✅ UI-05 static/js partagé (ml-optimizer-shared.js — 6 fonctions dédupliquées, 7 laissées séparées : divergence comportementale réelle constatée)
- ✅ UI-06 accessibilité clavier (17 éléments cliquables : role/tabindex/onkeydown)
- ✅ UI-08 renderAllocGrid partagé (dashboard/portfolio ; bots.html non migré — composant fonctionnellement différent)
- ✅ UI-10 SmcChart conditionnel (smc-chart.js — smartgraph/smartreplay seulement, les 6 autres pages chart ne l'utilisaient pas)
- ✅ UI-11 terminologie fr/en (Refresh→Actualiser, Rejections→Rejets)
- ✅ UI-07 attributs ARIA sur les 11 templates restants (aria-label boutons icône-seul, role=status/aria-live zones de chargement, scope=col th)
- ✅ UI-12 showSkeleton() partagé (API étendue opts.cards/opts.colspan, 8 sites convertis)
- ✅ UI-09 liens croisés data↔scanner (lien ↗ Analyser par ligne data.html ; lien → Charger les données sur erreur « insuffisantes » côté scanner.html ; lecture symbol/tf en URL au boot du scanner)
- ✅ TEST-02 tests/test_live_trader.py (12 tests : instanciation, _build_active_per_tf, reload_active_strategies/reload_strategies, status, _restore_open_positions, stop — MockExchange + sqlite jetable, zéro réseau)
- ✅ TEST-03 tests/test_api_routes.py (10 tests : data_status/refetch, fast_analysis, portfolio ×2 (sans/avec trader), strategy_performance ×4 (2/3-parties, format invalide, sans SessionLocal), + 1 test d'auth dédié sans contournement — `verify_api_key` neutralisé via `app.dependency_overrides` pour les 9 autres, obstacle non anticipé par la directive)
- ✅ TEST-07 tests/test_position_lifecycle.py (7) + tests/test_balance_sync.py (10) — 17 tests : ouverture/gating (halted, max_positions), gestion (gap→clôture forcée), clôture (PnL+persistance), sync paper/spot/margin, pré-exécution
- ✅ TEST-08 docstring `optimizer_results[strat][tf][symbol]` étendu dans `_load_strategy_configs` (app/core/config.py) — le vrai trou (resolve_strategy_params avait déjà d'excellents docstrings depuis V4-B)
- ✅ TEST-10 ARCHITECTURE.md — section « Live Trading Loop » (composition mixins, diagramme _cycle(), slots, cycle de vie, allocation), pointeurs croisés depuis les 2 diagrammes sommaires existants
- ✅ TEST-09 CHANGELOG.md — [Non publié] (817 lignes réelles, pas ~750) découpé en 10 versions datées (12.8.0→12.17.0, dates réelles via git log) par jalon ; [Non publié] → 3 lignes ; découpe scriptée et vérifiée lossless (git diff : 42 insertions, 0 suppression)
- ✅ TEST-12 requirements.txt — déjà entièrement épinglé (constat), vérifié par un vrai `pip install` dans un venv Python 3.12 vierge (0 conflit) + `pytest -q` (576 verts) ; date de lock + justification transitive-non-figée ajoutées en commentaire
- 🚫 TEST-11 (smoke tests ~40 stratégies) : hors-scope de cette passe, bloqué par DEAD-01 (tri du code mort, Vague 2/3, non demandé) — cf. directive originale du TEST-11.

## Conflits & dépendances à retenir

- **DEAD-08 vs UI-12** : opposés → décision = adopter `showSkeleton` (UI-12).
- **DEAD-03** : ne PAS exécuter sans accord explicite (les parquets sont
  aujourd'hui poussés volontairement par l'utilisateur depuis sa machine).
- **DEAD-04 + ARCH-08** : même livrable (`app/core/timeframes.py`) — une seule tâche.
- **TEST-11 après DEAD-01** ; **DEAD-07 après DEAD-01** (moins de fichiers à nettoyer).
- **UI-08/UI-10 après UI-05** (infrastructure static/js).
- **ARCH-03/11 après ARCH-02**.
- OPS-01 et ARCH-05 se complètent (helpers slot canoniques) — coordonner.

## Comment exécuter un item (protocole agent)

1. Lire l'item complet dans son fichier de domaine (Problème + Directive + Acceptation).
2. Créer une branche `claude/audit-<ID>` (ex. `claude/audit-bt-01`) depuis main.
3. Implémenter la directive, en respectant les règles transverses ci-dessus.
4. Vérifier le critère d'acceptation, lancer `python -m pytest -q`.
5. Commit `[<ID>] <titre>` + push + PR référençant ce document.
