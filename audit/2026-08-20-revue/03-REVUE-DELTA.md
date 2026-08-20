# 03 — Revue du delta `a6659e1..HEAD`

49 commits, 268 fichiers, +10 914 / −6 308, du 2026-08-18 22:40 au 2026-08-20.

Ce rapport suit les chemins critiques fichier par fichier. Le détail de chaque
constat vit dans le rapport de domaine ; on trouve ici la **lecture d'ensemble**
du delta et la localisation des défauts par commit.

---

## 1. Anatomie du delta

Deux phases nettement distinctes.

### Phase A — application de la revue du 18 août (18-19 août, ~20 commits)

`fec34ed`, `07fac77`, `80124b9`, `6300ef3`, `88f8bf8`, `6ddf83e`, `acca7dd`,
`0c3d9d9`, `e4a4ce0`, `f86d7a1`, `1897580`, `e3158c6`, `38516b6`.

Traitement du backlog de l'audit précédent : ARCH-01/02/03/04, SEC-02,
API-01/03, FE-01/02/03, ML-03/04/05, TEST-05, UX-01/02, FIN-11, PERF-05.

C'est ici que se concentrent **tous les défauts P1 de correctness** de ce
delta. Le commit `fec34ed` (« appliquer la revue-complete du 18 août ») porte à
lui seul quatre d'entre eux.

### Phase B — travail sur le Laboratoire (20 août, ~14 commits)

`29c4255`, `8d9f1bb`, `b9973b6`, `663f914`, `9aee1b8`, `b42d200`, `54e8a8c`,
`5b00e4d`, `d84bdf5`, `ccb53a8`, `ad9bb04`, `bb94993`.

Unification des sélecteurs, corrections d'affichage de l'Optimizer, double
passe de backtest, détection de trous. Presque entièrement frontend et
ergonomie ; un seul défaut P1 (`ccb53a8`, détection de trous).

### Répartition par zone

| Zone | Fichiers | Lignes |
|---|---:|---|
| `app/core` (dont paquets `risk/` et `smc/`) | 71 | +2 900 / −2 700 |
| `frontend/src` | 60 | +2 800 / −2 100 |
| `app/engine` | 18 | +560 / −250 |
| `tests/` | 24 | +900 / −40 |
| `app/strategies` | 41 | +120 / −110 |
| `app/live` | 9 | +200 / −60 |
| `app/ml` | 8 | +250 / −50 |
| `app/api` | 12 | +600 / −60 |

Les 41 fichiers de `app/strategies` ne portent que des retouches de typage
(`float = None` → `float | None`) : aucun changement de logique de stratégie.

---

## 2. Localisation des défauts par commit

| Commit | Constat | Sév. | Domaine |
|---|---|:--:|---|
| `fec34ed` | `FIN-01` — les frais des jambes et des pyramidages disparaissent du reporting | **P1** | Financier |
| `fec34ed` | `FIN-02` — le PnL journalisé ne retranche pas les frais d'entrée des pyramidages | **P1** | Financier |
| `fec34ed` | `OPT-01` — branches expectancy / profit factor inertes | **P1** | Optimiseur |
| `fec34ed` | `BT-01` — le walk-forward perd `realistic_risk` | **P1** | Backtest |
| `fec34ed` | `OPT-02` — garde-fou drawdown absent de la route apply manuelle | **P1** | Optimiseur |
| `fec34ed` | `ML-01` — le verdict `block` du garde-fou n'est pas appliqué | **P1** | ML |
| `ccb53a8` | `DAT-01` — seuil de détection des trous porté à 3×tf | **P1** | Données |
| `ccb53a8` | `PERF-01` / `DAT-03` — chemin calendaire jamais court-circuité | **P1** | Performance |
| (delta) | `CI-01` — `ruff check .` rouge | **P1** | CI |
| (delta) | `CI-02` — `mypy` rouge sur le périmètre CI | **P1** | CI |
| (delta) | `TEST-01` — aucun invariant sur les frais ni le capital | **P1** | Tests |
| `fec34ed` | `ML-02` — borne de Hanley à classes équilibrées | P2 | ML |
| `d84bdf5` | `UX-01/02/03` — accessibilité du `StrategyPicker` | P2 | UI/UX |
| (delta) | `API-01` — image Docker de test incomplète | P2 | API |
| (delta) | `API-02` — garde-fou de contrat limité aux noms | P2 | API |
| `07fac77` | `ARCH-01` — double identité `risk_*` / `risk.*` | P2 | Architecture |
| `fec34ed` | `LIVE-01` — alerte de stop trompeuse | P2 | Live |
| `fec34ed` | `OPT-03` — nouvelle forme du facteur de drawdown | P2 | Optimiseur |

**Le commit `fec34ed` concentre six des onze P1.** C'est un commit de
1 800 lignes touchant simultanément le moteur financier, l'optimiseur, le
backtest, le ML et le live. Sa taille est la cause directe de la densité de
défauts : chaque correction y est individuellement défendable, mais aucune n'a
pu être revue isolément.

---

## 3. Ce que le delta corrige réellement

Il serait faux d'en retenir seulement les défauts. Corrections de fond
vérifiées :

| Correction | Preuve |
|---|---|
| Faux trous de week-end sur les actions | 1 402 → **47** trous sur `AC.PA` 1d (données réelles) |
| Double comptage des frais de jambes dans le PnL | Conservation du capital exacte sans pyramidage (écart 1,8 e-5) |
| Warmup des folds OOS pris dans le fold | Préfixe de 210 barres, vérifié sans fuite (`warmup ≥ 210`) |
| Identifiant de stop exchange perdu en cas d'échec d'annulation | `pos["stop_order_id"]` restauré, `RuntimeError` remontée |
| `paper_mode` par défaut en mode réel | 6 occurrences basculées vers `True` |
| Cadence de réentraînement ML en horloge murale | Indexée sur ~200 barres, bornée [24 h, 14 j] |
| Fuite d'entraînement inline non mesurable | `app/ml/fit_trace.py` + `test_ml03_fit_causality.py` |
| Ordre `open`/`new` non rempli compté comme succès | `_order_failed` corrigé |
| 1 112 lignes de types frontend en un fichier | Éclaté en `generated.ts` / `views.ts` / `ui.ts` |
| `lib/api.ts` et `hooks/use-api.ts` sans test | 190 tests vitest, dont 6 fichiers neufs |

Le solde net du delta est **positif**. Les onze P1 sont des résidus et des
effets de bord, pas un effondrement.

---

## 4. Lecture d'ensemble : un motif récurrent

Six des onze P1 partagent la même forme — **un garde-fou est ajouté, mais rien
ne le relie à une décision** :

- `OPT-01` — `beats_baseline` accepte `oos_pf` / `oos_expectancy` ; ni le
  baseline ni les appelants ne les fournissent ;
- `OPT-02` — le garde-fou de drawdown existe ; la route la plus utilisée ne le
  déclenche pas ;
- `ML-01` — `validate_model_quality` renvoie `block` ; personne ne lit ce
  verdict pour décider ;
- `DAT-03` / `PERF-01` — une garde de chemin rapide existe ; sa condition
  (`cal is None`) n'est jamais vraie ;
- `BT-01` — `realistic_risk` devient configurable ; la clé n'est nulle part ;
- `TEST-01` — un fichier de test annonce une propriété de conservation ; aucun
  test ne l'exprime en égalité.

Le code exprime l'intention correcte ; le câblage manque. C'est la signature
d'un lot appliqué vite, sur une liste de points, sans vérification de bout en
bout de chaque point.

**Recommandation de méthode** — pour chaque garde-fou, exiger un test qui
**échoue** avant le correctif. Les six défauts ci-dessus auraient tous été
attrapés par cette seule règle, sans revue supplémentaire. C'est aussi le
critère d'acceptation que j'ai posé dans chaque fiche de délégation.

---

## 5. Ordre de traitement conseillé

Les correctifs se regroupent naturellement par fichier :

| Lot | Constats | Fichiers | Effort |
|---|---|---|---|
| **1 — CI au vert** | `CI-01`, `CI-02` | `tests/test_audit_a02_ml_data.py`, `app/core/database.py` | 10 min |
| **2 — Comptabilité** | `FIN-01`, `FIN-02`, `TEST-01` | `app/engine/position_lifecycle.py`, `tests/test_partial_exits.py` | 4 h |
| **3 — Trous et perf** | `DAT-01`, `DAT-03`, `PERF-01` | `app/core/ohlcv_gaps.py` (une seule fonction) | 1 h |
| **4 — Gates optimiseur** | `OPT-01`, `OPT-02`, `OPT-04` | `opt_scoring.py`, `auto_optimizer.py`, `routes/optimizer.py` | 2 h |
| **5 — Décisions de trading** | `BT-01`, `OPT-03`, `FIN-04` | à trancher par l'utilisateur | — |
| **6 — Gate ML** | `ML-01`, `ML-02` | `policy.py`, `overfitting_gate.py` | 4 h |
| **7 — Ergonomie** | `UX-01/02/03` | `strategy-picker.tsx` | 2 h |
| **8 — Outillage** | `API-01`, `API-02`, `LIVE-01`, `LIVE-02` | divers | 2 h |

Les lots 1 à 4 rendent le dépôt cohérent en **moins d'une journée**. Le lot 5
n'est pas un correctif : ce sont trois changements de paramétrage de trading
introduits par le delta, qui appellent une décision explicite plutôt qu'une
correction automatique.
