# 01 — Synthèse exécutive

**Dépôt** : `bot-crypto` · **HEAD** `3ca68a3` · **Date** 2026-08-18
**Périmètre** : 61 400 lignes Python (`app/`), 32 700 lignes TS/TSX (`frontend/src`),
29 100 lignes de tests · hors documentation et scripts indépendants, par consigne.

---

## Verdict

**Le code est meilleur que sa réputation d'un dépôt de trading de cette taille, et ses
défauts sont d'un genre inhabituel.** Je n'ai trouvé aucune fuite de données évidente,
aucune faille de sécurité exploitable, aucune formule monétaire fausse. Les fondations
sont justes : source unique des coûts partagée backtest ↔ live, ordres idempotents par
`clientOrderId`, holdout découpé en fin d'historique, `pickle` entièrement supprimé du
chargement des modèles, distinction « non mesurable » / « nul » propagée du backend jusqu'à
l'interface. Plusieurs de ces points sont mieux traités ici que dans la plupart des dépôts
comparables.

Les 74 constats se répartissent presque tous en **quatre motifs récurrents**, et c'est là
le résultat le plus utile de cette revue : il y a moins de 74 problèmes à corriger que
quatre habitudes à changer.

| Sévérité | Nombre |
|---|---:|
| **P0 potentiel — à instrumenter d'abord** | 1 |
| **P1 — majeur** | 23 |
| P2 — mineur | 38 |
| P3 — cosmétique ou latent | 11 |
| **Total** | **74** |

Dont **11 reproduits par exécution** (mesure ou profilage), le reste établi par lecture
croisée ou recensement exhaustif.

---

## Les quatre motifs

### Motif 1 — Le garde-fou qui se neutralise en silence

Six constats, même forme : un chemin dégradé est emprunté sans que la sortie le distingue
du chemin nominal.

| Constat | Le garde-fou | Ce qui se passe à la place |
|---|---|---|
| **OPT-04** | Le gate d'apply décide sur le holdout | Sans holdout, il décide sur `df_oos` — la tranche qui a classé les N essais |
| **OPT-05** | Gate walk-forward de consistance | Rend `True` dans ses **trois** cas d'échec |
| **BT-03** | 5 folds walk-forward | Un fold qui lève est retiré ; `n_folds` rapporte les survivants |
| **BT-09** | Split IS/OOS | Sous 310 barres, l'OOS est **vide** et rien ne le signale |
| **ML-03** | `ml_mode="frozen"` | Retombe sur un entraînement inline dans 4 situations |
| **PERF-01** | Série vectorisée mémoïsée | Repli O(n²) silencieux sur grille irrégulière |

Croisés, ils s'aggravent : **BT-01** fait que le walk-forward renvoie une erreur sous
1 560 barres, ce qui rend **OPT-05** *systématiquement* neutre en dessous de 260 jours
d'historique 4 h — pas occasionnellement.

**Le remède est unique et vaut pour les six** : distinguer *« garde-fou satisfait »* de
*« garde-fou non évaluable »*. Un garde-fou non évaluable doit bloquer une décision
automatique, jamais l'autoriser — et le dire dans la sortie, pas dans un log serveur.

### Motif 2 — La correction appliquée à un niveau et pas au niveau voisin

Sept constats où le bon raisonnement existe **dans le même fichier**, appliqué ailleurs.

| Constat | Fait correctement | Pas fait juste à côté |
|---|---|---|
| **FIN-02** | Spread + taker sur la clôture EOD (`backtest.py:576`) | Sorties `early_exit` / `exit_after_bars` : ni l'un ni l'autre |
| **FIN-05** | Plafond cumulé au niveau **venue** (F-05) | Plafond **slot** : agrégat tenu, jamais comparé |
| **FIN-03** | `slippage_cost` et `funding_cost` s'accumulent | `borrow_cost` est écrasé par la jambe finale |
| **ML-02** | Purge **et** embargo dans l'optimiseur | Purge seule dans `ml/splitting.py` |
| **SEC-01** | `err_id` opaque sur 5 routes | Message d'exception brut sur 8 autres |
| **LIVE-01** | Garde anti-stops-dupliqués à la restauration | Absent sur la remontée du trailing (des milliers de fois plus fréquente) |
| **LIVE-04** | `paper_mode` avec défaut sûr sur 3 sites | Sans défaut sur les ~20 qui décident d'un ordre réel |

**Le remède** : à chaque correctif, chercher les autres appelants de la même grandeur. Le
graphe de code répond à cette question en une requête.

### Motif 3 — La mesure validée sur des données qui n'existent pas

Deux constats, et c'est le plus coûteux du rapport.

**PERF-01** : `htf_trend_ema_series` rend `None` sur les données réelles du dépôt — 5 pas
irréguliers sur 15 768, dont un trou de 164 jours — et un tableau valide sur une grille
parfaitement régulière, celle que construisent les tests. L'optimisation ×120 du commit
`bfc330e` est donc **validée par les tests et inerte en production depuis son
introduction**. Mesuré : 58 barres/s contre 2 637 sur les mêmes données, soit **×45**, et
30 heures au lieu de 40 minutes pour une campagne de 400 essais.

**FIN-01** : le test censé protéger l'invariant vérifie
`net_profit == final_equity - initial_capital`, une identité **vraie par définition**.
L'invariant utile — `total_pnl == net_profit`, celui que le code revendique explicitement —
n'était testé par rien. Il est faux, exactement de la somme des frais de sortie des jambes
partielles.

**Le remède** : des fixtures de données réalistes (avec trous) partout où un test exerce un
chemin causal vectorisé, et un garde de débit sur série réelle tronquée.

### Motif 4 — Le contrat non typé

**ARCH-01** est la racine commune de quatre P1. `Backtester.run` a **86 appelants** et
retourne un dictionnaire de **45 clés** que rien ne décrit.

- **FIN-01** — le champ `fees` a changé de sémantique sans qu'aucune signature ne s'y oppose.
- **API-01** — 99 routes, **zéro** `response_model` : ce dict traverse le réseau tel quel.
- **FE-03** — 1 462 lignes de types recopiées à la main côté client pour le décrire.
- **BT-02** — `realistic_risk` est dans le dict, `_fold_summary` ne le remonte pas : deux
  résultats sont comparés alors qu'ils n'ont pas la même économie.

Quatre commits de la fenêtre auditée ne font que **réaligner ces types à la main**. Le
travail se répétera indéfiniment tant que le contrat ne sera pas déclaré une seule fois,
côté serveur.

---

## Les cinq constats qui comptent le plus

### 1. PERF-01 — ×45 de perte de débit, 30 heures par campagne d'optimisation

Profilé et diagnostiqué. `_htf_buckets` consomme 85 % du temps de backtest de 8 stratégies
parce que le **repli** de `htf_trend` n'est pas mémoïsé — seule la vérification l'est.
**Correction : ~30 lignes.** C'est le meilleur rapport effort/gain du rapport.

### 2. OPT-01/02 — Le gate Deflated Sharpe décide sur un nombre non interprétable

`trial_sharpes_std` n'est fourni par aucun appelant : la valeur `1.0` par défaut impose,
à 400 essais, un seuil implicite de Sharpe annualisé de **2,99**. Un Sharpe de 1,5 donne
DSR = 0,000005. Le seul choix de cette constante fait varier le résultat de **cinq ordres
de grandeur** — et l'optimiseur dispose de la mesure. S'y ajoute une incohérence d'échelle
(Sharpe annualisé fourni à une formule par observation).
**Action immédiate : `deflated_sharpe_gate: false`** en attendant la calibration.

### 3. FIN-01 — `total_pnl` ne vaut pas la variation d'équité, et le code affirme l'inverse

Reproduit : écart de −0,1928 sur un PnL de 136,34, égal à la somme des frais de sortie des
jambes. 38 % du poids du score de l'optimiseur en hérite, et le poste « runner » du tableau
par jambe absorbe **toute** l'erreur — la seule ligne biaisée est celle qu'on interroge.
**Correction : 1 ligne** (`position["entry_fees"]` existe déjà et n'est jamais pollué).

### 4. LIVE-01 — Deux stops vivants sur une même position

`_cancel_exchange_stop` retire `stop_order_id` **avant** l'appel réseau et rend `None` sur
échec — valeur indiscernable d'un succès. Le trailing repose alors un second stop. La
position porte deux ordres de vente déclenchables ; à la clôture, un seul est annulé.
L'orphelin peut ouvir un short non désiré.

### 5. BT-01 — Les folds OOS du walk-forward sont consommés à 81 % par leur warmup

Au minimum admis (260 barres/fold), **49 barres seulement sont tradées**. La `consistency`
qui alimente le gate d'auto-apply est calculée sur des échantillons de 0 à 3 trades, et une
stratégie sélective est pénalisée pour n'avoir pas eu le temps de trader.

Et une sixième, à instrumenter avant tout le reste :

### ML-03 — La causalité de l'entraînement inline n'est pas établie

Le mode `frozen` (défaut) retombe sur un entraînement inline dans quatre situations, dont
l'absence de modèle publié — le cas nominal au premier usage. Si cet entraînement reçoit le
DataFrame complet plutôt qu'une fenêtre glissante, **tous les backtests ML publiés sont à
rejeter**. Une demi-journée de mesure tranche la question ; je ne l'ai pas faite et je ne
conclus pas.

---

## Répartition par domaine

| Rapport | P0? | P1 | P2 | P3 | Total |
|---|---:|---:|---:|---:|---:|
| `04-FINANCIER` | — | 3 | 6 | 2 | 11 |
| `05-BACKTEST` | — | 2 | 7 | 1 | 10 |
| `06-OPTIMISEUR` | — | **5** | 3 | — | 8 |
| `07-LIVE-EXECUTION` | — | 2 | 2 | 2 | 6 |
| `08-ML` | **1** | 1 | 2 | — | 5* |
| `09-BACKEND-API` | — | 1 | 3 | 1 | 5 |
| `10-FRONTEND` | — | 2 | 1 | 1 | 4 |
| `11-UI-UX` | — | 1 | 3 | — | 4 |
| `12-DONNEES` | — | — | 2 | 2 | 4 |
| `13-PERFORMANCE` | — | 2 | 1 | — | 3 |
| `14-SECURITE` | — | **0** | 2 | 1 | 3 |
| `15-TESTS-CI` | — | 3 | 2 | 1 | 6 |
| `03-ARCHITECTURE` | — | 1 | 4 | — | 5 |
| **Total** | **1** | **23** | **38** | **11** | **74** |

\* dont un constat déjà corrigé, listé pour mémoire.

**La sécurité est le seul domaine sans P1.** Ce n'est pas un hasard : c'est le domaine où
les mécanismes ont été traités dans le détail plutôt qu'en surface.

---

## Plan d'action

### Immédiat — 8 lignes, 5 constats fermés

| Action | Constat | Effet |
|---|---|---|
| `deflated_sharpe_gate: false` dans `config/lifecycle.yaml` | OPT-01/02 | Retire un gate non interprétable du chemin de production |
| `entry_fees = position.get("entry_fees", position.get("fees", 0.0))` | FIN-01 | Rétablit `total_pnl == net_profit` |
| `"borrow_cost": position.get("borrow_cost", 0.0) + borrow` | FIN-03 | Cesse de perdre l'emprunt des jambes |
| `--cov-fail-under=64` | TEST-01 | Transforme un plancher décoratif en contrainte |
| `.get("paper_mode", True)` sur les ~20 sites | LIVE-04 | Défaut sûr là où la décision se prend |

### Cette semaine — le meilleur rendement

| # | Action | Constats | Effort |
|---|---|---|---|
| 1 | Mémoïser le repli de `htf_trend`, vérifier `bb_squeeze_series` | PERF-01, PERF-02 | 30 lignes |
| 2 | Instrumenter la causalité de l'entraînement inline | **ML-03** | ½ j de mesure |
| 3 | Distinguer « satisfait » de « non évaluable » sur les 4 gates | OPT-04, OPT-05, BT-03, BT-09 | 1 j |
| 4 | Corriger `_cancel_exchange_stop` (3 issues au lieu de 2) | LIVE-01 | 3 h |
| 5 | `_order_failed` doit refuser `filled == 0` | LIVE-02 | 2 h |
| 6 | Un critère de drawdown dans `beats_baseline` ; `dd_factor` sans saturation | OPT-03 | 1 j |
| 7 | Seuil de couverture frontend + tests de `lib/api.ts` et `use-api.ts` | TEST-02, FE-01 | 3 j |

### Le mois — le chantier structurant

**`BacktestResult` en dataclass typée** (ARCH-01). Deux à trois jours qui ferment FIN-01
par construction, alimentent API-01 sans travail supplémentaire, rendent FE-03 générable
et exposent `realistic_risk` là où BT-02 en a besoin. C'est le seul chantier du rapport
dont le rendement dépasse largement son coût.

Puis, dans l'ordre :
- Un `Protocol` par famille de mixins : **−344 erreurs mypy** pour ~60 lignes (ARCH-04).
- Découper `_manage_open_position` en trois fonctions pures : rend FIN-02, FIN-06 et FIN-08
  testables isolément (ARCH-02).
- Fixtures de données réalistes + garde de débit sur série réelle (TEST-05, PERF-03).
- `formatMoney(value, currency)` propagé depuis `quote_currency` (UX-01).

### À traiter à part — les correctifs qui changent les chiffres

Deux corrections **modifient le PnL de tout backtest existant**. Elles sont justes, et
c'est précisément pourquoi elles méritent leur propre branche, avec revalidation des
paramètres déjà retenus par l'optimiseur :

- **FIN-02** — spread + taker sur les sorties `early_exit` / `exit_after_bars`
  (0,11 % du notionnel par trade, soit 18 à 37 % de l'espérance d'une stratégie à 30-60 bps).
- **BT-01** — warmup amont sur les folds OOS du walk-forward.

---

## Ce qui est bien fait

Un audit qui ne liste que les défauts donne une image fausse. Ce qui suit a été vérifié,
et tient.

**Financier** — `execution.py` est bien la source unique : les 4 sites de clôture (2
backtest, 2 live) y passent tous, avec la venue. Funding et emprunt sont correctement
disjoints (un perp ne s'emprunte pas ; le funding est signé et encaissable par un short).
Le fill au gap est symétrique stop/TP. Le stop l'emporte toujours sur le TP en ambiguïté
intrabar — et l'ambiguïté est *comptée* au lieu d'être ignorée. `RiskLedger.reserve` est
atomique, refuse la double réservation, sans tolérance de dépassement.

**Live** — `create_order` est **idempotent** : `clientOrderId` généré avant l'envoi, ordre
recherché par cet identifiant après un timeout et **réutilisé**. C'est le risque le plus
grave d'un bot de trading, et il est traité correctement. Retry différencié selon la
criticité de l'appel. Venue `can_execute: false` interceptée au plus près du trade.
Comptage en bougies et non en horloge (un week-end XPAR n'est pas 62 barres 1 h).

**Backtest** — la boucle est causale sur tous les chemins vérifiés. Warmup dynamique
propagé au benchmark. Le Sharpe refuse d'inventer une durée pour s'annualiser, et vaut
`None` — non `0.0` — sous 10 observations. Le holdout est découpé à la fin de l'historique
et retiré de tout le pipeline de recherche.

**Optimiseur** — `overfitting_ratio` rend `NaN` plutôt qu'une valeur qui prendrait rang
dans un classement, et le `NaN` est correctement absorbé en aval. La pénalité de
surapprentissage ne s'applique plus aux scores négatifs (où elle *récompensait* le
surapprentissage). Le score est monotone avec le PnL. Un seul seuil de trades entre
sélection et promotion.

**ML** — plus aucun `pickle` ni `joblib` : une classe entière de RCE fermée. Registre
versionné avec résolution `as_of` et invalidation par chevauchement. `splitting.py`
supprime une fuite réelle dans les trois entraîneurs à la fois, en annonçant honnêtement
l'effet attendu (« *une légère baisse des AUC — c'est la correction d'un biais, pas une
régression* »).

**Sécurité** — `hmac.compare_digest`, repli fermé hors localhost, `X-Forwarded-For` derrière
liste blanche de proxys, jeton borné avant comparaison, en-tête de corrélation assaini
contre l'injection de log, cookie `HttpOnly; SameSite=Lax; Secure`, `pip-audit` bloquant,
dépendances épinglées avec la date de verrouillage documentée. Aucun `eval`, `exec`,
`shell=True`. **Zéro P1.**

**Données** — écriture Parquet **atomique** (`.tmp` + `os.replace`), verrous par fichier,
bougie en formation supprimée à la source, détection de trous consciente du calendrier de
marché.

**Interface** — `MetricValue` distingue « pas de valeur », « non significatif » et
« infini », avec infobulles : le contrat `None ≠ 0.0` traverse toute la pile, du
`BacktestResult` jusqu'au pixel. La suite d'accessibilité ouvre **chaque onglet Radix
séparément**, parce que Radix ne monte que l'onglet actif — c'est le piège dans lequel
tombent la plupart des suites a11y. `tsc --noEmit` : 0 erreur sur 32 700 lignes.

**Tests** — 2 075 tests, zéro échec, 154 s. Une suite de cette taille utilisable en boucle
de développement. Et `tests/test_partial_exits.py` est un modèle de structure : une
docstring qui énonce les trois propriétés vérifiées, puis un test par propriété, sur des
données déterministes. C'est ce fichier qui m'a servi à reproduire FIN-01.

**Culture d'ingénierie** — les messages de commit portent la **raison**, pas l'action :
« *max_dd_p95 est le pire drawdown, pas le meilleur* », « *l'overfit résiduel n'existait
pas — la métrique était fausse* », « *un short à levier 1 emprunte toujours l'actif* ». Un
commit de la fenêtre consigne même « *deux bugs trouvés, trois prédictions démenties* ».
C'est ce qui a rendu cette revue possible en trois jours de code plutôt qu'en trois
semaines — et c'est la pratique la plus précieuse du dépôt.

---

## Réserves sur cette revue

- **ML-03 n'est pas tranché.** C'est le constat au plus fort enjeu et je n'ai pas fait la
  mesure. Il est signalé comme question ouverte, pas comme défaut.
- **PERF-02 est déduit d'une structure de code identique**, pas mesuré : isoler la
  contribution de `bb_squeeze` demande un profilage dédié de `breakout`.
- **Les stratégies elles-mêmes ne sont pas auditées sur le fond.** J'ai vérifié la
  causalité et le coût de leurs appels, pas la pertinence de leurs signaux — ce n'est pas
  une question de code.
- **Les 38 P2 ne sont pas tous instrumentés.** Ils sont établis par lecture ou recensement ;
  leur statut de preuve est indiqué constat par constat.
- **La documentation existante n'a pas été lue**, par consigne. Si un constat de ce rapport
  y est déjà décrit et arbitré, l'arbitrage prime — dites-le et je le retire.
