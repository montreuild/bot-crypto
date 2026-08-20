# 02 — Revue de code du delta des 3 derniers jours

**Fenêtre** : 86 commits du 2026-08-15 au 2026-08-18 · base `bfc330e^` → `3ca68a3`
**Volume** : 243 fichiers, **+15 067 / −18 154** lignes (hors `CHANGELOG.md`, `docs/`, `audit/`)

## Répartition

| Zone | Fichiers | Ajouts | Suppressions |
|---|---:|---:|---:|
| `frontend/` | 89 | +4 852 | −4 131 |
| `app/engine` | 20 | +3 773 | −1 880 |
| `tests/` | 52 | +2 761 | −66 |
| `app/strategies` | 15 | +1 038 | −663 |
| `app/core` | 21 | +924 | −264 |
| `app/ml` | 11 | +518 | −50 |
| `scripts/` *(hors périmètre)* | 6 | +498 | −10 |
| `app/api` | 9 | +294 | −337 |
| `app/live` | 8 | +227 | −87 |
| `data/` | 1 | +1 | −10 629 |
| CI / config | 6 | +129 | −13 |

**16 nouveaux modules dans `app/`** : `position_lifecycle`, `backtest_result`,
`opt_bayesian`, `opt_budget`, `opt_freeze`, `compute_pool`, `compute_jobs`,
`smc_patterns/{__init__,composites,journal,stats}`, `ohlcv_gaps`, `splitting`, `threads`,
`retrain`, `bt_predictions`, `smart_money_setups`.
**11 nouveaux fichiers frontend**, dont 5 composants d'optimiseur et le découpage
`backtest-view` / `backtest-results` / `smart-graph-*`.

---

## Appréciation générale

Trois jours à ce rythme sur du code financier est un régime dangereux, et le résultat est
meilleur qu'attendu. Deux choses expliquent pourquoi :

1. **Le ratio tests/code est bon** : +2 761 lignes de tests sur 52 fichiers pour +6 500
   lignes de code applicatif. La discipline « un correctif, un test » est réelle et
   vérifiable commit par commit.
2. **Les messages de commit portent la raison, pas l'action.** `8fba261`
   (« *max_dd_p95 est le pire drawdown, pas le meilleur* »), `782dd98` (« *l'overfit
   résiduel n'existait pas — la métrique était fausse* »), `d4b8038` (« *un short à levier 1
   emprunte toujours l'actif* ») : chacun énonce le fait corrigé. C'est ce qui a rendu cette
   revue possible en trois jours de code plutôt qu'en trois semaines.

La faiblesse est ailleurs, et elle est structurelle au rythme : **les correctifs de mesure
ne sont pas vérifiés sur les données réelles**. Deux des constats les plus lourds de cette
revue (PERF-01, FIN-02) sont des correctifs de la fenêtre qui ne produisent pas l'effet
annoncé, et dans les deux cas la cause est la même — le test qui les valide n'exerce pas
les conditions de production.

---

## Par thème

### A. Performance — 5 commits, un gain majeur inerte

| Commit | Objet |
|---|---|
| `bfc330e` | `htf_trend` coûtait O(n²) — 8 stratégies, jusqu'à ×120 |
| `4830ef9` | `bb_squeeze` en série causale — `breakout` ×80 |
| `229cb4c` | Prédiction par lot v4/v5/`ml_dynamic_threshold`, ADX pré-calculé |
| `a68e364` | Threads LightGBM selon le contexte — ×3,1 |
| `bed4751` | L'énumération des composés SMC était refaite une fois par composé |

**Verdict : la mécanique est juste, l'effet ne se produit pas sur les données du dépôt.**

`bfc330e` introduit une mémoïsation correcte dans `htf_trend`. Mesuré (cf.
`13-PERFORMANCE.md`, PERF-01) : `htf_trend_ema_series` rend `None` sur BTC_USDC 4 h — 5 pas
irréguliers sur 15 768, dont un trou de 164 jours — et le **repli, lui, n'est pas
mémoïsé**. Le `None` est mis en cache, la vérification n'est plus repayée, mais
`_htf_buckets` est recalculé à chaque barre. Profil : 85 % du temps, 2,3 M réductions numpy.

Débit mesuré : **58 barres/s** (`trend`) contre **2 637** (`volatility_squeeze`) sur les
mêmes 15 769 barres. Le ×120 annoncé est validé par les tests, qui utilisent des grilles
synthétiques régulières, et **inerte en production depuis son introduction**.

`4830ef9` a la même structure de repli (`bb_squeeze_series`, 4 chemins `return None`) : à
vérifier dans le même passage.

**À faire** : mémoïser le repli, pas seulement la vérification. ~30 lignes pour récupérer
un facteur 45.

---

### B. Modes de sortie et sorties partielles — 3 commits, un défaut d'agrégation introduit

| Commit | Objet |
|---|---|
| `5334495` | Modes de sortie génériques et testables (`EXIT_MODES`) |
| `9085d48` | Le PnL de trade porte les frais d'entrée |
| `2ce004b` | Fill au gap, stop sur bougie en formation, `exit_mode` live |

**Verdict : très bonne conception, une régression comptable non détectée.**

`apply_exit_mode` est bien pensé : cinq modes nommés, `as_declared` par défaut (donc aucune
régression), un mode inconnu **refusé bruyamment** plutôt qu'ignoré, et `_sans_cible_fixe`
qui retire les deux chemins par lesquels une cible arrive — avec la démonstration chiffrée
de pourquoi c'est nécessaire (`smc_ml_edge` fixait sa cible à 1R, pile où TP1 devait se
déclencher, ce qui rendait `tp1_tp2_runner` identique à `as_declared`). C'est du travail de
qualité.

Mais `9085d48` (« le PnL de trade porte les frais d'entrée ») fait lire à `_close_at` la clé
`position["fees"]` en la traitant comme les seuls frais d'entrée — ce qui était vrai avant
les jambes partielles et ne l'est plus, puisque `_close_partial_at` fait grossir cette même
clé. **FIN-01, reproduit** : l'invariant `total_pnl == net_profit` que `to_dict()` revendique
explicitement est rompu, exactement de la somme des frais de sortie des jambes.

Le test associé (`test_partial_exits.py::test_l_equite_finale_reste_coherente`) vérifie
`net_profit == final_equity - initial_capital`, une identité **vraie par définition** — il
ne pouvait rien détecter. L'invariant utile était `total_pnl == net_profit`.

`2ce004b` livre `_fill_at_level` (fill au gap, symétrique stop/TP), qui est juste. Il laisse
en revanche les sorties `early_exit` et `exit_after_bars` sortir au close exact, sans spread
et en frais maker — **FIN-02**, alors que le même fichier applique explicitement spread et
taker à la clôture de fin de série, avec le bon raisonnement en commentaire.

---

### C. Coûts et emprunt — 4 commits, tous justes

| Commit | Objet |
|---|---|
| `400a1c8` | Plus d'intérêt d'emprunt à levier ≤ 1 |
| `d4b8038` | Un short à levier 1 emprunte toujours l'actif |
| `b95ab6a` | Enveloppe venue, drawdown MTM, DSR |
| `ebad658` | Embargo branché, enveloppe opt, seed, `val_*` |

**Verdict : corrects, et la séquence `400a1c8` → `d4b8038` est exemplaire.**

Le premier commit supprime l'intérêt d'emprunt à levier ≤ 1 ; le second **corrige la
correction** en observant qu'un short emprunte l'actif quel que soit le levier — cas que le
premier garde avait effacé à tort. La docstring de `borrowed_notional` (`execution.py:31-38`)
énonce les trois cas séparément. C'est la bonne façon de rattraper une sur-correction.

`ebad658` branche l'embargo (`default_purge_embargo`) dans `auto_optimizer` et renomme les
champs de la tranche de sélection en `val_*` — *« O-01 : la tranche de sélection n'est plus
hors-échantillon, alias `val_*` pour le nommer honnêtement »* (`optimizer_search.py:341-346`).
Renommer un champ pour cesser de mentir sur ce qu'il mesure est un geste rare et bon.

Réserve : l'embargo est branché dans l'optimiseur et **pas** dans `ml/splitting.py`, qui
n'applique que la purge (ML-02). Deux modules, deux conventions, et c'est le ML qui a la
plus faible.

---

### D. Métriques et surapprentissage — 4 commits, deux vraies corrections et un gate non calibré

| Commit | Objet |
|---|---|
| `8fba261` | `max_dd_p95` est le pire drawdown, pas le meilleur |
| `7e68213` | Sharpe `None` sous 10 observations |
| `782dd98` | L'overfit résiduel n'existait pas — la métrique était fausse |
| `b95ab6a` | Deflated Sharpe au gate de naissance |

**Verdict : trois excellentes corrections, un gate à désactiver en attendant sa calibration.**

`8fba261` corrige un signe conceptuel : le 95ᵉ percentile d'une série de drawdowns négatifs
est le **meilleur** cas, pas le pire. Le commentaire de `monte_carlo.py:68-71` explique le
raisonnement complet. Ce genre d'erreur est invisible en relecture et coûteux en décision.

`782dd98` est la meilleure correction de la fenêtre. `overfitting_ratio` rendait `0.0` —
*la meilleure valeur de l'échelle* — quand le score IS était négatif, et saturait à `10.0`
dès que le score OOS passait sous 0,01. Deux configurations opposées (`multi_tf_sr` ETH 4 h
à +371,7 de PnL OOS et `fear_momentum` BTC 1 h à −168,4) recevaient le même `overfit = 0.0`.
La nouvelle version rend `NaN` pour les trois cas dégénérés, et `_penalized_score` teste
`np.isnan` avant toute comparaison. Le raisonnement est juste de bout en bout.

`7e68213` distingue `None` (non mesurable) de `0.0` (ratio nul) sous
`MIN_SIGNIFICANT_TRADES`, et le frontend suit (`MetricValue`).

En revanche, le **gate Deflated Sharpe** activé par `b95ab6a` (défaut `true`, seuil 0,5)
appelle `deflated_sharpe_ratio` sans `trial_sharpes_std`, donc toujours avec le défaut
`1.0` — jamais mesuré, alors que l'optimiseur dispose de tous les Sharpe d'essais. Calculé
(cf. `06-OPTIMISEUR.md`, OPT-01) : à `max_trials: 400`, le seuil implicite est un Sharpe
annualisé de **2,99** ; un Sharpe de 1,5 donne DSR = 0,000005 ; et le seul choix de la
constante fait varier le résultat de **cinq ordres de grandeur**. S'y ajoute une
incohérence d'échelle (Sharpe annualisé fourni à une formule par observation, `sqrt(t-1)`
annualisant une seconde fois).

**À faire, immédiatement** : `deflated_sharpe_gate: false` en attendant. Un gate qu'on ne
sait pas interpréter est pire qu'un gate absent, parce qu'il donne l'illusion d'une
protection.

---

### E. Risque et registre — 5 commits, la parité reste incomplète

| Commit | Objet |
|---|---|
| `431e38e` | Refuse une réserve sur une clé déjà engagée (L-05) |
| `98ca3ef` | B-02 multi-positions, A-07 `session_scope` |
| `58c0f0f` | Plus de plafond 25 % caché, clôture atomique |
| `9381177` | A-05/B-14 payload, S-05 caviardage, L-13 verrou |
| `2b73800` | A-04 tickers courts, D-01/D-02 Parquet, L-09 notionnel paper |

**Verdict : bons correctifs, mais le registre reste asymétrique entre backtest et live.**

`431e38e` ferme une fuite permanente de budget (une seconde réserve sur la même clé
écrasait la première sans décrémenter les agrégats). `58c0f0f` supprime un plafond de 25 %
non documenté et rend la clôture atomique. Le multi-positions (`98ca3ef`) est propre côté
moteur.

Trois écarts subsistent, tous dans le même sens — le backtest en fait moins que le live :

- **FIN-04** : le backtest n'appelle **jamais** `ledger.update_risk` quand le trailing
  remonte le stop, alors que le live le fait (`position_manage_mixin.py:215`). Recherche
  exhaustive des appelants : aucun dans `app/engine/`.
- **FIN-07** : `_close_partial_at` ne redimensionne pas la réservation ; le live fait
  `resize` sur ce chemin.
- **FIN-05** : `_slot_notional` / `_slot_risk` sont maintenus et **jamais confrontés à un
  plafond** — exactement le défaut corrigé un niveau au-dessus par F-05, dont le commentaire
  est encore dans le fichier.

Le multi-positions a aussi laissé deux compteurs de diagnostic faux (`trades_closed`,
`max_bars_in_position` ne détectent la transition que quand la **dernière** position se
ferme — `backtest.py:460-464`), et un commentaire devenu faux à cet endroit précis.

---

### F. Optimiseur — 6 commits, une architecture qui progresse, des gates qui s'ouvrent

| Commit | Objet |
|---|---|
| `4b96928` | Le gate d'apply se prononce sur une tranche jamais vue (holdout) |
| `2722e77` | Budget proportionné à l'espace — `smart_money` 40 → 400 |
| `8f0eefb` | Le gate manuel et le fetch honorent le holdout |
| `d8a4a15` | O-05 gel prudent, O-08/O-10/O-11, B-04 stabilité |
| `03131d5` | X-03 optimizer/SMC |
| `601f43f` | U-08 optimizer-view |

**Verdict : le holdout est la bonne construction, ses replis l'annulent.**

Le holdout découpé en fin d'historique et retiré de tout le pipeline de recherche (y compris
du walk-forward, via `df_recherche`) est méthodologiquement correct, et
`is_oos.py:27-33` explique parfaitement pourquoi il était nécessaire.

Deux replis en défont l'effet :

- **OPT-04** : `df_gate = df_holdout if df_holdout is not None else df_oos`
  (`auto_optimizer.py:300`). Sans holdout — cas fréquent sur historique court, et
  précisément celui où le sur-apprentissage est le plus probable — le gate décide sur la
  tranche qui a servi à classer les N essais. Le module qui décrit le défaut fournit le
  repli qui le réintroduit.
- **OPT-05** : `_wf_consistent` rend `True` dans ses **trois** cas d'échec. Croisé avec
  **BT-01** (le walk-forward renvoie `{"error"}` sous 1 560 barres), le gate est
  *systématiquement* neutre en dessous de 260 jours d'historique 4 h — pas
  occasionnellement.

`2722e77` (budget 40 → 400) est justifié en soi, mais interagit avec OPT-01 : `n_trials`
alimente le seuil DSR, qui passe de 2,19 à 2,99. Chercher mieux durcit mécaniquement la
promotion.

---

### G. Frontend et accessibilité — 89 fichiers, du bon travail

| Commit | Objet |
|---|---|
| `2fa8052` | Contraste WCAG AA du thème clair |
| `74f056e` | Baselines visuelles Linux thème clair AA |
| `9913bd2` | Split backtest / Smart Graph, U-05 types |
| `601f43f` | U-08 `optimizer-view` |
| `ff99731` | Type `FoldResult`, contraste a11y |

**Verdict : le meilleur bloc de la fenêtre.**

Le découpage est réel (`backtest-view` / `backtest-results`, `smart-graph-view` /
`-tables` / `-helpers`, 5 composants d'optimiseur extraits), `tsc --noEmit` passe à 0
erreur, `eslint` à 0 erreur, et le contraste AA est corrigé **avec** régénération des
baselines visuelles — c'est-à-dire jusqu'au bout.

`MetricValue` (nouveau dans la fenêtre) est le pendant côté interface du travail
`None ≠ 0.0` fait côté backend : trois états distincts, avec infobulles explicatives. La
cohérence de la pile sur ce point est remarquable.

Deux réserves :
- Les fonctions résultantes du découpage restent **au-dessus de 500 lignes**
  (`SmartReplayView` 656, `useSmartGraphChart` 609, `SmartGraphView` 551,
  `BacktestResults` 521, `BacktestView` 516). Le découpage a séparé les fichiers, pas les
  fonctions.
- Quatre commits successifs (`e85ccf3`, `9df222f`, `9913bd2`, `601f43f`) ne font que
  **réaligner des types à la main** entre backend et frontend. C'est le symptôme d'API-01
  (zéro `response_model`), pas un problème de frontend : ce travail se répétera.

---

### H. CI et outillage — 6 commits, un progrès réel et un seuil resté décoratif

| Commit | Objet |
|---|---|
| `8631972` | GitLab CI (miroir de GitHub Actions) |
| `435b2af` | `eslint` à la place de `next lint`, CI lint job-card, ruff `BacktestResult` |
| `572abf8` | eslint, Node 22 et types CI dans `audit/16` |
| `11c06d7` | Ruff I001/F401 pour débloquer le lint CI |
| `a3a67d3` | Ruff I001 `test_risk_curve` |
| `72d3ca1` | `test.describe.skip` en CI |

**Verdict : bon, sauf que les seuils ne mordent pas.**

Le miroir GitLab est fidèle (jobs identiques, vérifié un par un), le passage de `next lint`
à `eslint` anticipe la dépréciation, `interruptible: true` et le cache `npm ci` sont bien
posés.

Mais aucun de ces commits ne touche aux deux chiffres qui compteraient :
`--cov-fail-under=25` alors que la couverture réelle est de **66 %** (TEST-01), et **aucun
seuil de couverture frontend** alors qu'elle est à **4,84 %** (TEST-02). Le job mypy reste
sur 3 fichiers sur 206, en `continue-on-error`.

---

### I. Analyse statistique SMC — nouveau sous-système

`6eddecd` (analyse statistique des motifs SMC/ICT, multi-TF, avec témoins) + `bed4751`
(perf) + 3 fichiers de tests (`test_smc_patterns_composites`, `_journal`, `_stats` — 463
lignes).

**Verdict : correctement construit.** `smc_patterns/composites.py:21` applique le découpage
`split_with_holdout` aux motifs plutôt qu'aux paramètres — la transposition est juste. Les
graines sont fixées (`default_rng(graine)`), les témoins sont présents. Le module est
nouveau et testé d'emblée.

À noter sans jugement : `0285cc0` (« bilan d'application — **deux bugs trouvés, trois
prédictions démenties** ») est un commit de documentation qui consigne des prédictions
réfutées. C'est une pratique qui vaut d'être conservée.

---

## Ce que cette revue du delta recommande

Par ordre de priorité, en ne retenant que ce qui découle **directement** de la fenêtre :

| # | Action | Constat | Effort |
|---|---|---|---|
| 1 | `deflated_sharpe_gate: false` en attendant sa calibration | OPT-01/02 | 1 ligne |
| 2 | Lire `position["entry_fees"]` au lieu de `position["fees"]` dans `_close_at` | FIN-01 | 1 ligne |
| 3 | Mémoïser le repli de `htf_trend` (et vérifier `bb_squeeze`) | PERF-01/02 | 30 lignes |
| 4 | Refuser l'auto-apply sans holdout ; distinguer gate satisfait / non évaluable | OPT-04/05 | 15 lignes |
| 5 | Appliquer spread + taker aux sorties `early_exit` / `exit_after_bars` | FIN-02 | 2 lignes ⚠ change les backtests |
| 6 | Warmup amont sur les folds OOS du walk-forward | BT-01 | 5 lignes ⚠ change les WF |
| 7 | `--cov-fail-under=64` et un seuil frontend | TEST-01/02 | 2 lignes |

Les points 1, 2, 3, 4 et 7 sont des correctifs de justesse ou de configuration sans effet
sur les chiffres de trading déjà produits. Les points **5 et 6 changent le PnL** de tout
backtest existant : à isoler dans leurs propres branches, avec revalidation des paramètres
retenus par l'optimiseur.
