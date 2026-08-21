# 99 — Registre global des constats

Audit du 2026-08-20 sur `bb94993`. Delta audité : `a6659e1..HEAD`.

> **État des corrections : [`98-SUIVI.md`](98-SUIVI.md).**
> Ce registre décrit les constats *tels que trouvés*. 28 sur 35 sont
> traités, dont les 11 P1.

**35 constats** : 0 P0, 11 P1, 15 P2, 9 P3.
**23 CONFIRMÉS par exécution**, 4 PLAUSIBLES (lecture seule), 8 améliorations
vérifiées.

---

## Répartition

| Sévérité | Nombre | Dont reproduits |
|---|---:|---:|
| **P0** — bloquant | 0 | — |
| **P1** — majeur | 11 | 11 |
| **P2** — mineur | 15 | 13 |
| **P3** — cosmétique | 9 | 7 |

| Statut | Nombre |
|---|---:|
| CONFIRMÉ (reproduit par exécution ou mesure) | 23 |
| PLAUSIBLE (lecture du code seule) | 4 |
| Amélioration vérifiée (pas un défaut) | 8 |

---

## P1 — majeurs

| ID | Constat | Fichier:ligne | Statut | Effort | Rapport |
|---|---|---|:--:|---:|---|
| `FIN-01` | `_close_at` écrase `position["fees"]` : frais des jambes partielles et des pyramidages perdus du reporting (−11 % à −24 % mesurés) | `app/engine/position_lifecycle.py:65,77` | CONFIRMÉ | 1 h 30 | 04 |
| `FIN-02` | Le PnL journalisé ne retranche pas les frais d'entrée des pyramidages : Σ`pnl` diverge de la courbe d'équité (+0,4634 mesuré) | `app/engine/position_lifecycle.py:75` | CONFIRMÉ | 1 h | 04 |
| `DAT-01` | `max_gap_seconds` porte le seuil de détection de trous de 1,5×tf à 3×tf : 15 trous réels masqués sur `BTC_USDC` 1h, 4 sur 4h | `app/core/ohlcv_gaps.py:71-74` | CONFIRMÉ | 45 min | 09 |
| `PERF-01` | `detect_ohlcv_gaps` ×1,75 global, jusqu'à ×5,53 ; chemin calendaire jamais court-circuité ; 386 s de surcoût sur le parc | `app/core/ohlcv_gaps.py:52` | CONFIRMÉ | 35 min | 13 |
| `OPT-01` | Branches expectancy et profit factor de `beats_baseline` inertes : ni le baseline ni les appelants ne fournissent les valeurs | `app/engine/opt_scoring.py:221-232` | CONFIRMÉ | 1 h 30 | 06 |
| `OPT-02` | Garde-fou drawdown non câblé sur la route d'application manuelle : DD OOS 80 % contre baseline 10 % accepté | `app/api/routes/optimizer.py:349` | CONFIRMÉ | 20 min | 06 |
| `BT-01` | Le walk-forward passe de `realistic_risk=True` figé à une clé de config absente : le gate ne compare plus à régime de risque égal | `app/engine/walk_forward.py:69,98` | CONFIRMÉ | 30 min | 05 |
| `ML-01` | Le verdict `block` de `validate_model_quality` n'alimente qu'un log : il ne modifie jamais `gate.decision` | `app/ml/policy.py:321-338` | CONFIRMÉ | 1 h | 08 |
| `CI-01` | `ruff check .` échoue sur HEAD (3× I001) — job `lint` rouge | `tests/test_audit_a02_ml_data.py:69,86,103` | CONFIRMÉ | 2 min | 15 |
| `CI-02` | `mypy` échoue sur le périmètre CI exact (1 erreur) — job `mypy` rouge | `app/core/database.py:336` | CONFIRMÉ | 5 min | 15 |
| `TEST-01` | Aucun test d'invariant sur les frais ni sur le capital — c'est ce qui a laissé passer `FIN-01` et `FIN-02` | `tests/test_partial_exits.py:196-215` | CONFIRMÉ | 2 h | 15 |

---

## P2 — mineurs

| ID | Constat | Fichier:ligne | Statut | Effort | Rapport |
|---|---|---|:--:|---:|---|
| `ML-02` | La borne de Hanley suppose `n1=n0=n/2` : garde-fou trop permissif sur labels déséquilibrés (6 scénarios sur 7) | `app/ml/overfitting_gate.py:29-48` | CONFIRMÉ | 3 h | 08 |
| `OPT-03` | Le facteur de drawdown passe de linéaire à hyperbolique : un DD de 40 % n'élimine plus | `app/engine/opt_scoring.py:86` | CONFIRMÉ | — | 06 |
| `API-01` | L'image Docker de test ne copie ni `scripts/gen_frontend_types.py` ni `generated.ts` | `Dockerfile:109-110` | CONFIRMÉ | 15 min | 10 |
| `API-02` | Le garde-fou anti-dérive des contrats ne compare que les noms d'interfaces, pas les champs | `tests/test_openapi_contracts.py:46` | CONFIRMÉ | 30 min | 10 |
| `ARCH-01` | Double identité `risk_*`/`risk.*` et `smc_*`/`smc.*` : 68 sites contre 35 | `app/core/_compat.py:16` | CONFIRMÉ | 2 h | 02 |
| `LIVE-01` | L'alerte de stop non remplacé annonce « niveau plus prudent » alors que l'exposition augmente | `app/live/position_manage_mixin.py:399` | CONFIRMÉ | 20 min | 07 |
| `LIVE-02` | `_order_failed` traite un statut inconnu non rempli comme un succès | `app/live/position_open_mixin.py:90` | PLAUSIBLE | 30 min | 07 |
| `UX-01` | `StrategyPicker` sans `aria-pressed` ni `role="group"` : état non annoncé, porté par la seule couleur | `frontend/src/components/ui/strategy-picker.tsx:72-92` | CONFIRMÉ | 1 h | 12 |
| `UX-02` | Libellés rendus à 8,8 px (`text-[0.55rem]`) | `frontend/src/components/ui/strategy-picker.tsx:87,98` | CONFIRMÉ | 30 min | 12 |
| `UX-03` | L'avertissement de TF non recommandé n'est accessible que par survol (`title` sur un `span`) | `frontend/src/components/ui/strategy-picker.tsx:95-102` | CONFIRMÉ | 45 min | 12 |
| `SEC-01` | Registre de jetons WS local au processus — sans conséquence en mono-processus actuel | `app/api/ws_tickets.py:18-19` | CONFIRMÉ | 15 min | 14 |
| `TEST-02` | Périmètre mypy CI : 110 fichiers sur 227 ; 347 erreurs hors périmètre | `.github/workflows/ci.yml:34` | CONFIRMÉ | 3-5 j | 15 |
| `DETTE-01` | 10 `datetime.utcnow()` dépréciés, sur les chemins de persistance | `app/engine/opt_persistence.py:72,153,208` | CONFIRMÉ | 2 h | 16 |
| `DAT-04` | `_delta_seconds` lève sur une valeur temporelle nulle | `app/core/ohlcv_gaps.py:56` | PLAUSIBLE | 10 min | 09 |
| `PERF-02` | Chaque sauvegarde de bougies rescanne tout l'historique | `app/core/candle_store.py:1019` | CONFIRMÉ | 5 min | 13 |

---

## P3 — cosmétiques

| ID | Constat | Fichier:ligne | Statut | Effort | Rapport |
|---|---|---|:--:|---:|---|
| `OPT-04` | Terme `oos_wr > b_wr` redondant dans la première condition | `app/engine/opt_scoring.py:226` | CONFIRMÉ | 5 min | 06 |
| `ARCH-02` | `smart-replay-view.tsx` (744) et `backtest-results.tsx` (681) non découpés | `frontend/src/components/views/` | CONFIRMÉ | 8 h | 02 |
| `ARCH-03` | `build_features` : sortance 354, aucun test de contrat sur le schéma produit | `app/ml/backend/features.py:1` | PLAUSIBLE | 1 h | 02 |
| `BT-03` | Le mode ML des folds est forcé à `"frozen"`, une surcharge de config n'est plus prise en compte | `app/engine/walk_forward.py:98` | CONFIRMÉ | — | 05 |
| `FE-02` | La reconnexion WS redemande un jeton à chaque tentative | `frontend/src/lib/ws-provider.tsx:93` | CONFIRMÉ | — | 11 |
| `SEC-02` | Pas de limite de débit sur `POST /api/ws/ticket` ; purge linéaire sous verrou | `app/api/routes/ws.py:197` | CONFIRMÉ | 1 h | 14 |
| `DETTE-04` | 11 fichiers Python > 700 lignes | — | CONFIRMÉ | — | 16 |
| `ML-03b` | `fit_trace` est par thread : un entraînement délégué à un autre thread ou processus n'est pas tracé | `app/ml/fit_trace.py:17` | PLAUSIBLE | 1 h | 08 |
| `FIN-04` | Le pyramidage applique désormais la courbe de risque et le frein de volatilité — changement de paramétrage | `app/engine/position_lifecycle.py:400-408` | CONFIRMÉ | — | 04 |

---

## Améliorations vérifiées (pas des défauts)

| ID | Ce que le delta corrige | Preuve | Rapport |
|---|---|---|---|
| `DAT-02` | Faux trous de week-end sur les actions | 1 402 → 47 sur `AC.PA` 1d, données réelles | 09 |
| `BT-02` | Warmup des folds OOS pris dans le fold | Préfixe 210 barres, vérifié sans fuite (`warmup ≥ 210`) | 05 |
| `BT-04` | Folds échoués silencieusement ignorés | `n_folds_failed` remonté et exploité par le gate | 05 |
| `LIVE-03` | Identifiant de stop exchange perdu en cas d'échec d'annulation | Restauré, `RuntimeError` remontée | 07 |
| `LIVE-04` | `paper_mode` par défaut en mode réel | 6 occurrences basculées vers `True` | 07 |
| `ML-03` | Fuite d'entraînement inline non mesurable | `fit_trace.py` + `test_ml03_fit_causality.py` | 08 |
| `ML-04` | Cadence de réentraînement en horloge murale | Indexée sur ~200 barres, bornée [24 h, 14 j] | 08 |
| `ML-05` | Embargo `chrono_split` | `max(purge, 1 % de la série)` — strictement plus sûr | 08 |

---

## Décisions de trading en attente (ni défauts, ni correctifs automatiques)

Trois changements du delta modifient le comportement de trading. Ils appellent
une décision explicite de l'utilisateur, pas une correction :

| ID | Changement | Effet |
|---|---|---|
| `BT-01` | `realistic_risk` du walk-forward | Circuit breakers actifs ou non dans le gate d'auto-apply |
| `OPT-03` | Forme du facteur de drawdown | Plus sévère sous 15 %, beaucoup plus permissif au-delà de 30 % |
| `FIN-04` | Courbe de risque et frein de volatilité au pyramidage | Modifie les résultats de toutes les stratégies qui pyramident |

---

## Plan de traitement

| Lot | Constats | Effort cumulé | Effet |
|---|---|---:|---|
| **1 — CI au vert** | `CI-01`, `CI-02` | 10 min | Débloque toute PR |
| **2 — Comptabilité** | `FIN-01`, `FIN-02`, `TEST-01` | 4 h 30 | Le PnL et les coûts redeviennent justes |
| **3 — Trous et perf** | `DAT-01`, `DAT-03`, `PERF-01`, `DAT-04` | 1 h 30 | Détection rétablie, 386 s économisées |
| **4 — Gates optimiseur** | `OPT-01`, `OPT-02`, `OPT-04` | 2 h | Les garde-fous ajoutés s'appliquent enfin |
| **5 — Décisions trading** | `BT-01`, `OPT-03`, `FIN-04` | — | À trancher par l'utilisateur |
| **6 — Gate ML** | `ML-01`, `ML-02` | 4 h | Le refus de modèle devient effectif |
| **7 — Ergonomie** | `UX-01`, `UX-02`, `UX-03` | 2 h 15 | Sélecteur accessible |
| **8 — Outillage** | `API-01`, `API-02`, `LIVE-01`, `LIVE-02` | 1 h 35 | Docker de test vert, contrats verrouillés |
| **9 — Fond** | `TEST-02`, `ARCH-01`, `DETTE-01` | 4-6 j | Dette structurelle |

**Les lots 1 à 4 rendent le dépôt cohérent en moins d'une journée** (8 h 10) et
traitent 9 des 11 P1.
