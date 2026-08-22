# 98 — Suivi des constats

État au 2026-08-21. Le registre (`99-REGISTRE.md`) décrit les constats **tels
que trouvés** le 20 août ; ce document dit ce qui a été livré depuis.

**28 constats traités sur 35.** Les 11 P1 sont clos.

---

## P1 — tous livrés

| ID | Livré par | Vérification |
|---|---|---|
| `CI-01` | `28b0ff9` | `ruff check .` vert |
| `CI-02` | `28b0ff9` | `mypy` périmètre CI vert |
| `FIN-01` | `0bee794` | écart de frais 0,192823 → **0,000000** |
| `FIN-02` | `0bee794` | Σ`pnl` − équité +0,4634 → **−4e-5** (arrondi) |
| `TEST-01` | `0bee794` | 2 invariants en égalité, 3 stratégies |
| `DAT-01` | `93ed667` puis `83c03c9` | BTC 1h : 20 trous, aucun masqué ; `max_gap_seconds` retiré de la détection |
| `PERF-01` | `93ed667` | ×1,75 plus lent → **×3,4 plus rapide** qu'avant le delta |
| `OPT-01` | `862c0fb`, `6a982cc` | un candidat PF+expectancy est accepté |
| `OPT-02` | `862c0fb` | DD 80 % vs 10 % → **409** sur la route manuelle |
| `BT-01` | `35061bd` | résolveur unique, symétrie testée |
| `ML-01` | `e5b0406` | le verdict `block` ramène la décision à `keep` |

## P2 — livrés

| ID | Livré par | Note |
|---|---|---|
| `ML-02` | `db3d50c` | effectifs réels par classe ; 6 scénarios de déséquilibre bloquent |
| `OPT-03` | `6a982cc` | plafond absolu de drawdown, aligné sur `trading.max_drawdown_global` |
| `API-01` | `59aa260` | image Docker de test complétée |
| `API-02` | `59aa260` | comparaison champ par champ ; une dérive réelle (`alpha_vs_bh`) corrigée |
| `ARCH-01` | `caaf06b` | 90 imports migrés, 15 shims + `_compat` supprimés |
| `LIVE-01` | `59aa260` | alerte de stop : sens du risque rétabli |
| `LIVE-02` | `59aa260` | statut inconnu = échec ; `_order_rejected` séparée pour les ordres au repos |
| `UX-01/02/03` | `0948ad2` | `aria-pressed`, `role="group"`, nom accessible complet |
| `SEC-01` | `59aa260` | contrainte mono-processus écrite et testée |
| `DAT-04` | `59aa260` | horodatage nul : paire ignorée avec log |
| `DETTE-01` | `89ec019` | 14 `utcnow()` ; avertissements 128 → 46 |
| `TEST-02` | `187fd43`, `5416f92`, `66b4cbe`, cette PR | `app/live` 176→0, `app/ml` 40→0, `app/api` 26→0, `app/strategies` 117→0 ; **tout `app/`** au job CI |
| `PERF-02` | `07bc133` | détection de trous incrémentale à la sauvegarde — ×138 |
| `SEC-02` | `8e86393` | limite de débit sur `POST /api/ws/ticket` |
| `ML-03b` | `8e86393` | `fit_trace` partagé entre threads |
| `BT-03` | cette PR | résolveur unique `resolve_ml_mode` ; `frozen` mesuré inexploitable sur 0/5 folds |
| `FE-02` | cette PR | pas de handshake sans jeton : un aller-retour perdu par cycle au lieu de deux |

## P3 — livrés

| ID | Livré par |
|---|---|
| `OPT-04` | `862c0fb` — condition simplifiée |
| `ARCH-03` | `89ec019` — contrat de schéma `build_features` (462 colonnes) |

---

## Ouvert depuis — traitement des trous en aval

Quatre constats consignés le 2026-08-21 dans
[`18-TROUS-EN-AVAL.md`](18-TROUS-EN-AVAL.md). Ils préexistaient ; le travail sur
la détection de trous les a rendus visibles. Trois sont livrés.

| ID | Sév. | Constat | État |
|---|---|---|---|
| `DOWN-01` | **P1** | `bars_per_year` applique une convention 24/7 aux actions : Sharpe annualisé ×2, CAGR ×3,9 (BNP.PA 15 m) | `848c634` — annualisation calendaire ; BNP.PA 15 m 35 040 → 8 670 barres/an, BTC/USDC inchangé |
| `DOWN-02` | P2 | La complétude n'est consommée par aucun module en aval | `848c634` — portée dans le résultat et l'UI, avec avertissement ; **pas de seuil** (choix explicite) |
| `DOWN-03` | P2 | La durée d'une position vient du compte de barres — sans effet sur les actions (spot), −7 % à −45 % en margin/perp | `848c634` — durée lue sur les horodatages réels |
| `DOWN-04` | P2 | Aucun contrôle de continuité temporelle ; indicateurs positionnels | ouvert — 2 h à plusieurs jours |

---

## Reste ouvert

| ID | Sév. | Constat | Effort |
|---|---|---|---|
| `ARCH-02` | P3 | `smart-replay-view.tsx` (744 l.), `backtest-results.tsx` (681 l.) | 8 h |
| `DETTE-04` | P3 | 11 fichiers Python > 700 lignes | — |

---

## Trouvailles hors périmètre, corrigées en route

Douze défauts qu'aucun constat d'audit ne visait, rencontrés en corrigeant les
autres :

| Où | Défaut |
|---|---|
| `_place_exchange_stop` | `_order_failed` appliquée à la **pose** d'un stop : toute pose réussie sur un exchange renvoyant `status="open"` était déclarée en échec — le bot renonçait à protéger la position, ou annulait un stop actif (`59aa260`) |
| `AutoOptMixin` | Héritait d'`OptimizerHost`, le contrat du moteur d'optimisation, alors que c'est un mixin de `LiveTrader`. Seul `cfg` est commun aux deux, ce qui masquait l'erreur (`187fd43`) |
| `LiveHost` | `_margin_interest` déclaré `dict` alors que c'est un `float` ; `signal_log` déclaré `list` alors que c'est un `deque` (`187fd43`) |
| `generated.ts` | `alpha_vs_bh` ajouté à la main faute de déclaration côté Pydantic — le contrat vivait en désaccord avec sa source (`59aa260`) |
| `health_mixin` | Deux variables `eq` dans la même portée : une courbe d'équité et un cumul de PnL, la seconde masquant la première (`187fd43`) |
| `/api/ml/registry/decisions/recent` | `list_versions(None, recipe)` levait un `TypeError` avalé par l'`except` englobant : la route ne renvoyait **jamais** une décision. 175 décisions accessibles après correction (vérifié sur le conteneur) (cette PR) |
| `mypy.ini` | Les sections `[mypy-app.ml]`, `[mypy-app.live]` ne visaient que le `__init__.py` du paquet, pas ses modules : `check_untyped_defs` y était **inerte** alors que `CONTRIBUTING.md` l'annonçait actif. Corrigé en `[mypy-app.*.*]`, étendu à `app/core` et `app/engine` (cette PR) |
| `save_model` × 4 | `opus_omnibus_v11`, `scoring_statistique_opus_v4/v5`, `ml_dynamic_threshold` redéclaraient `save_model(path)` SANS `extra_meta` : un appelant qui passait la provenance ML-02 recevait un `TypeError`, et aucun artefact de ces stratégies n'a jamais porté sa provenance. `extra_meta` traverse désormais `TrainedRecipe.save` et `save_lgb_with_scaler` (cette PR) |
| `managed_externally` | `BaseStrategyML` le déclarait en attribut simple, `MLBackendMixin` en propriété déléguant au backend : sur `class Strategy(MLBackendMixin, BaseStrategyML)` l'attribut de base était mort. Converti en propriété des deux côtés (cette PR) |
| `_signal_at(cal_targets)` | Annoté `List[float]`, alimenté par `cal_tp: List[Tuple[float, str]]` et dépaqueté en couple — l'annotation décrivait l'inverse du contrat réel (cette PR) |
| `_classify_regime(bb_rank)` | Annoté `float` alors que son propre corps teste `bb_rank is not None` et que l'appelant lui passe un `row.get(...)` non gardé, seul de ses six arguments (cette PR) |
| `_candle_add`, caches ATR/ADX | Invariants « posés et effacés ensemble » testés sur un seul membre : `pin_a` gardé mais `eng_a` indexé, `_w_atr` gardé mais `_w_adx`/`_w_close_ref` indexés (cette PR) |
| `/api/data/backfill-equities` | La boucle appelait `provider.fetch_bars`, méthode inexistante : `AttributeError` avalée par symbole, job « done » avec 0 bougie partout, aucun Parquet écrit. Même correcte, la lecture directe du provider n'aurait rien persisté — c'est le store qui écrit (cette PR) |

---

## Décisions de trading tranchées

Le registre les laissait à l'arbitrage. Résolues **sur preuve**, dans le sens
de la cohérence backtest/live :

**`FIN-04` — pyramidage soumis à la courbe de risque : conservé.**
`RiskSizer.compute_size` applique déjà le frein de volatilité et la courbe de
drawdown, et le pyramidage **live** passe par là. Le changement ne durcit pas
le backtest : il supprime une divergence backtest/live.

**`OPT-03` — score hyperbolique : conservé, mais ancré.**
L'ancienne forme linéaire s'annulait à 30 %, rendant 35 % et 90 % de drawdown
indiscernables. Score lisse pour classer, gate dur pour admettre — et le gate
a reçu un **plafond absolu** (`6a982cc`) aligné sur `trading.max_drawdown_global`,
qui supprime l'effet cliquet du seuil purement relatif.

**`BT-01` — `realistic_risk` rétabli à `True`, des deux côtés.**
Un résolveur unique (`app.core.is_oos.resolve_realistic_risk`) sert au baseline
**et** au walk-forward. Le live applique ses circuit breakers ; un walk-forward
qui les ignore promet un comportement que le live ne reproduira pas.

---

## Ce qui a changé dans la façon de travailler

Les onze P1 partageaient une forme : **un garde-fou écrit, mais relié à aucune
décision**. La règle retenue — et inscrite dans `CONTRIBUTING.md` — est qu'un
correctif de garde-fou s'accompagne d'un test qui **échoue avant** lui. Elle a
été appliquée à chacun des correctifs de ce suivi.

Deux fois, cette règle a rattrapé une erreur de ma part : un `aria-label` qui
remplaçait le texte visible (cassant la commande vocale, WCAG 2.5.3), et un
retrait trop large dans la détection de trous qui faisait remonter 1 376 faux
positifs sur les actions.
