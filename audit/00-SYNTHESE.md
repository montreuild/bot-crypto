# Audit complet — Synthèse

> ⚠️ **RÉVISÉ le 17 août 2026** — voir
> [`13-REVISION-2026-08-17.md`](13-REVISION-2026-08-17.md).
> Cinq constats ont été résolus depuis (O-01 requalifié, **O-02**, **O-03**,
> O-07, M-02), quatre nouveaux sont apparus (N-01 à N-04). **Les 8 autres
> constats critiques sont intacts**, revérifiés ligne par ligne sur `32d6c90`.
> Le tableau des dix corrections du §3 reste valide : aucune n'a été traitée.

**Dépôt** : `bot-crypto` — bot de trading algorithmique multi-stratégies
(Python 3.14 / FastAPI / polars / LightGBM + Next.js 15 / React 19)
**Périmètre** : ~131 000 lignes (58 k Python applicatif, 46 k frontend,
26 k tests) + 827 Mo de données persistées
**Base auditée** : `d62c487` (PR #221) — révisée sur `32d6c90` (PR #228)
**Date** : 14 août 2026, révisé le 17 août 2026
**Méthode** : lecture du code source seul. Conformément à la demande, ni la
documentation (`docs/`, 35 k lignes), ni le `CHANGELOG.md`, ni les scripts
indépendants (`scripts/`, `research/`) n'ont été utilisés comme source de
vérité. Les commentaires ont servi à comprendre l'intention, jamais à valider
un comportement. Toutes les affirmations chiffrées sont vérifiées sur le code ou
sur les données réelles de `data/`.

---

## 1. Le constat central

Ce dépôt est **bien construit**. L'architecture en couches est réellement
respectée, les formules monétaires ont une source unique partagée
backtest ↔ live, la configuration refuse de démarrer sur une incohérence, la
sécurité de l'API est sérieusement traitée, il y a 1 723 tests, et la qualité
des commentaires d'intention est supérieure à ce qu'on rencontre
habituellement — plusieurs sections de cet audit n'auraient pas été possibles
sans eux.

Le problème n'est donc pas la qualité de la construction. **C'est que la chaîne
qui mesure la performance est fausse à chacun de ses maillons, et que chaque
maillon suivant s'appuie sur le précédent sans le vérifier.**

```
   backtest              métriques            optimiseur           promotion
      │                     │                     │                    │
 stops remplis       total_pnl exclut       objectif = score      gates évalués
 au niveau même      les frais d'entrée     OOS  →  l'OOS         sur la fenêtre
 en cas de gap             │                n'est plus hors       qui a servi à
      │                Sharpe sur           échantillon           sélectionner
 mono-position         1-3 trades                │                     │
      │                     │                    │                     │
      └─────────────────────┴────────────────────┴─────────────────────┘
                                     │
                            allocation de capital
                            (poids d'enveloppe par edge)
```

Trois conséquences pratiques :

1. **Un backtest optimiste.** Les stops sont remplis au niveau exact même
   quand la bougie ouvre au-delà (B-01) ; le drawdown ne voit jamais les pertes
   latentes (F-06) ; les circuit breakers du live ne sont jamais simulés sur les
   chemins de décision (B-07) ; le backtest ne gère qu'une position à la fois
   alors que le live en gère plusieurs en concurrence (B-02).

2. **Des métriques qui ne mesurent pas ce que leur nom dit.** `total_pnl`
   exclut les frais d'entrée et sert pourtant de PnL de référence partout
   (F-01) ; le Sharpe est calculé sur 1 à 3 observations et sort à ±1 000
   (F-02) ; le p95 de drawdown du Monte-Carlo renvoie le **meilleur** cas
   (F-03).

3. **Une sélection circulaire.** L'optimiseur maximise le score sur la tranche
   OOS, puis les gates de promotion (`beats_baseline`, walk-forward) sont
   évalués sur cette même tranche (O-01, O-02, O-03).

Le dispositif censé rattraper tout cela — le forward-test, qui compare le live
à la simulation — **n'a jamais rendu un seul verdict** : les 254 slots de
`data/oos_tracker.json` portent tous `verdict: "pas_assez_de_trades_reels"` et
`live.n_trades: 0`.

---

## 2. Preuves chiffrées

Toutes vérifiées sur les données réelles du dépôt.

| Observation | Mesure | Rapport |
|---|---|---|
| Runs de backtest à \|Sharpe\| > 10 | **104 / 158** (max **1 014,76** sur 2 trades) | F-02 |
| Encore vrai sur la campagne la plus récente (2026-08-07) | 14 / 20 | F-02 |
| `sim.sharpe` de `oos_tracker` à \|·\| > 10 | 119 / 254 (max **4 050,9**) | F-02 |
| `max_dd_p95` **inférieur** au drawdown réellement observé | **145 / 155** | F-03 |
| Monte-Carlo dégénéré (`p5 == p95`, `prob_profit ∈ {0,100}`) | 146 / 155 | F-03 |
| Slots avec le moindre trade live enregistré | **0 / 254** | D-07 |
| Edges mesurées significativement positives (`ci_low > 0`) | 24 / 177 | D-07 |
| Runs de backtest sur moins de 10 trades | 74 / 158 | F-02 |
| Modules ≥ 40 lignes sans aucun test | **34** | T-02 |
| Recouvrement `scoring_statistique_opus_v5` ↔ `_v4` | **80 %** (478/600 lignes) | X-02 |
| Usages de `any` / `as any` dans le frontend | 212 | U-05 |
| Composants `'use client'` | 98 / 122 | U-04 |
| Consommateurs du module i18n | **0** | U-01 |

---

## 3. Les dix corrections qui comptent

Classées par rapport valeur / coût. Les cinq premières sont indépendantes et
livrables une par une.

| # | Correction | Fichier | Effort | Effet |
|---|---|---|---|---|
| 1 | `np.percentile(max_dds, **5**)` au lieu de `95` | `engine/monte_carlo.py:96` | **1 ligne** | Le principal indicateur de risque de séquence cesse de publier le meilleur cas comme s'il était le pire |
| 2 | `sharpe = None` sous 10 observations | `engine/backtest.py:211-227` | ~5 lignes | Supprime les Sharpe à 1 000 de toutes les décisions et de l'UI |
| 3 | Faire de `net_profit` la référence, pas `total_pnl` | `engine/backtest.py`, `opt_scoring.py` | ~1 j | Le signe du score, le gate de promotion et l'alpha cessent d'ignorer les frais d'entrée |
| 4 | Refuser la réservation si `pos_key` existe déjà | `core/risk_ledger.py:98` | **2 lignes** | Supprime une fuite permanente de budget engagé |
| 5 | Une seule transaction à la clôture | `live/position_close_mixin.py:270-329` | ~2 h | Supprime la fenêtre où un trade exécuté n'est enregistré nulle part |
| 6 | Remplir au gap quand la bougie ouvre au-delà du stop | `engine/backtest.py:928-944` | ~1 j | Supprime le biais optimiste le plus important du backtest (critique pour les actions) |
| 7 | Trois tranches : train / validation / test | `core/is_oos.py`, `optimizer_search.py`, `auto_optimizer.py` | ~3 j | Rend les gates de promotion réellement hors échantillon |
| 8 | Ne pas facturer d'emprunt à levier ≤ 1 | `core/execution.py:26-38` | ~2 h | Supprime ≈ 30 %/an de coût fictif sur la venue par défaut ⚠️ change tous les backtests |
| 9 | Supprimer le plafond caché `notional > capital × 0,25` | `live/balance_sync.py:196` | ~1 h | Le live cesse de refuser silencieusement les trades dimensionnés à l'enveloppe |
| 10 | Évaluer le stop sur le plus-bas/plus-haut de la bougie en formation | `live/position_manage_mixin.py:217` | ~1 j | Aligne les trois modèles d'exécution (backtest / paper / live) |

**Avertissement sur l'ordre** : les corrections 3, 6 et 8 modifient les
résultats de tous les backtests historiques. Elles doivent être livrées
**séparément**, chacune avec un re-baselining explicite de
`data/backtest_history.json` et `data/oos_tracker.json` — et ces fichiers
doivent gagner un `schema_version` + `git_commit` (D-06) pour que l'UI cesse
d'afficher côte à côte des mesures produites par des versions différentes du
calcul.

---

## 4. Répartition des constats

**131 constats** sur 12 rapports (dont ~10 croisés entre rapports).

| Sévérité | Nombre | Signification |
|---|---|---|
| Sévérité | 14 août | **17 août** | Signification |
|---|---|---|---|
| 🔴 Critique | 10 | **8** | Fausse une décision de trading ou peut faire perdre de l'argent / des données |
| 🟠 Majeur | 39 | **40** | Fausse une mesure, casse la parité, ou expose à un incident |
| 🟡 Moyen | 57 | **57** | Incohérence réelle, effet borné ou conditionnel |
| 🔵 Mineur | 25 | **25** | Hygiène, lisibilité, angle mort d'observabilité |

### Les dix constats critiques

| Réf | Titre | Rapport | État au 17/08 |
|---|---|---|---|
| F-01 | `total_pnl` exclut les frais d'entrée mais pilote score, sélection et promotion | [Financier](01-FINANCIER.md) | 🔴 ouvert |
| F-02 | Sharpe calculé sur 1 à 3 observations — 104/158 runs à \|Sharpe\| > 10 | [Financier](01-FINANCIER.md) | 🔴 ouvert |
| F-03 | `max_dd_p95` renvoie le meilleur drawdown, pas le pire | [Financier](01-FINANCIER.md) | 🔴 ouvert |
| B-01 | Stops et TP remplis au niveau, jamais au gap | [Backtest](02-BACKTEST.md) | 🔴 ouvert |
| B-02 | Backtest mono-position : `RiskLedger` et concurrence jamais exercés | [Backtest](02-BACKTEST.md) | 🔴 ouvert |
| O-01 | L'objectif d'optimisation **est** le score OOS | [Optimiseur](03-OPTIMISEUR.md) | 🟡 requalifié — reste un défaut de nommage |
| O-02 | `beats_baseline` évalué sur la fenêtre de sélection | [Optimiseur](03-OPTIMISEUR.md) | ✅ **résolu** (PR #222) |
| O-03 | Le gate walk-forward tourne sur les données de sélection | [Optimiseur](03-OPTIMISEUR.md) | ✅ **résolu** (PR #222) |
| L-01 | Le stop live n'est évalué qu'une fois par cycle, sur le dernier prix | [Live](04-LIVE.md) | 🔴 ouvert |
| L-02 | En paper (défaut), aucun stop exchange et aucun stop intrabar | [Live](04-LIVE.md) | 🔴 ouvert |

Deux nouveaux constats majeurs sont entrés le 17 août, tous deux sur le
correctif de holdout lui-même : **N-01** (`required_total_bars` ignore les 20 %
du holdout, donc le repli sans holdout se déclenche trop souvent) et **N-02**
(le bouton « Appliquer » de l'UI décide encore sur la tranche de sélection).
Détail dans [la révision](13-REVISION-2026-08-17.md).

---

## 5. Ce que le dépôt fait bien

Cette section n'est pas de la politesse : ces propriétés sont des **acquis à
préserver** pendant les corrections.

**Conception**

- Source unique des formules monétaires (`core/execution.py`) réellement
  partagée backtest ↔ live, avec test de parité dédié.
- Enveloppes de risque emboîtées venue → symbole → slot, avec un `RiskLedger`
  thread-safe, réservation atomique avant l'ordre, et restitution du budget
  quand le trailing remonte le stop.
- Configuration découpée par responsabilité, **refus de chargement** si une
  section est déclarée deux fois, écritures UI routées vers le fichier
  propriétaire.
- `app/core` ne dépend d'aucun module `app/engine` ni `app/live` — contrainte
  réelle et tenue.

**Correction méthodologique**

- Aucun look-ahead sur le prix d'entrée du backtest (signal sur barre close,
  entrée à l'ouverture suivante) — l'erreur la plus fréquente, évitée.
- Registre ML avec `as_of` : un backtest ne peut pas charger un modèle
  postérieur à sa fenêtre, et le chevauchement résiduel est **détecté**.
- Gate de promotion ML : candidat et sortant scorés sur le **même holdout
  aveugle**. C'est un vrai gate.
- Triple barrier causal, barrières en ATR, **convention pessimiste** quand une
  bougie touche cible et stop.
- Split chronologique strict et médianes d'imputation calculées sur le train
  seul — aucune fuite par le prétraitement.
- `MIN_SIGNIFICANT_TRADES` unifié, avec la justification binomiale écrite.

**Robustesse**

- `RobustExchange` : retry différencié par type d'exception ccxt, reset de
  session TCP après N erreurs.
- Watchdog dead-man en **process séparé**, heartbeat écrit atomiquement,
  kill-switch fichier.
- Kill-switch d'équité persistant et non levable sans acquittement explicite.
- Une position dont on n'a pas la preuve qu'elle est fermée est **remise en
  gestion**, jamais supprimée.
- Maintenance isolée de la boucle de trading.

**Sécurité**

- Refus de démarrage bloquant si `web.host: 0.0.0.0` sans `web.api_key`, avec
  override en variable d'environnement seulement (le YAML est ignoré).
- `hmac.compare_digest` partout, bornage de longueur, anti-spoofing
  `X-Forwarded-For` (`TRUSTED_PROXIES` vide par défaut), anti-log-forging.
- Auth sur les 20 routers sans exception. Clé API jamais dans le bundle client.
- Aucun `eval`/`exec`/`pickle`/`shell=True`, aucune injection SQL possible.
- Docker multi-stage, `USER bot` non root, `HEALTHCHECK`.
- CI à 4 jobs dont `pip-audit` sur les dépendances de prod **et** de dev.

**Frontend**

- `QueryBoundary` / `useStickyError` : le piège du `refetchInterval` court qui
  fait clignoter les erreurs est identifié et correctement résolu.
- Proxy same-origin qui injecte la clé côté serveur et supprime le CORS.
- Validation Zod non bloquante, timeout de 15 s sur chaque requête.
- Vitest avec seuils de couverture à 60 %, Playwright avec suite
  d'accessibilité axe-core.

---

## 6. Les trois angles morts

Au-delà des constats individuels, trois manques structurels.

**a. Rien ne mesure l'écart entre le simulé et le réel.** Le dispositif existe
(`forward_test.py` + `oos_tracker.py`, avec cône Monte-Carlo, bande de
confiance et verdict), il est bien conçu — et il n'a **jamais produit un seul
verdict** faute de trades live. Tant que ce chiffre reste à zéro, aucun des
correctifs de cet audit ne pourra être validé autrement que par raisonnement.
**C'est la priorité au-dessus de toutes les autres** : faire tourner le bot en
paper suffisamment longtemps pour que `live.n_trades > 0` sur quelques slots,
et regarder le verdict.

**b. Du code écrit, testé, et jamais appelé.** Six modules complets sont
inertes : le système i18n (0 consommateur), les circuit breakers de backtest
(`realistic_risk=False` partout), le Deflated Sharpe conforme (la version
maison est câblée à sa place), `run_dual_pass`, `venue_envelope`,
`overfitting_gate` (appelé après la décision). S'y ajoute du code qui
s'exécute sans rien conclure : la détection de fuite ML qui logge un WARNING et
laisse passer, le débordement d'enveloppe venue qui est **testé** au lieu
d'être empêché. Chacun donne l'illusion qu'une garantie existe.

**c. Les tests vérifient la forme, pas le sens.** L'illustration est
`test_monte_carlo.py:32` : `assert res["max_dd_p95"] >= 0.0` — toujours vrai à
cause du `abs()` de l'implémentation. C'est ainsi que le bug de percentile
inversé a survécu à 1 723 tests. La correction générale est d'écrire, pour
chaque grandeur financière, l'assertion qui exprime sa **définition** :
`max_dd_p95 >= |drawdown réalisé|`, `Σ pnl == final_equity − initial_capital`,
`Σ release == Σ reserve`, `borrow_cost == 0` quand rien n'est emprunté.

---

## 7. Index des rapports

| Rapport | Constats | 🔴 | Contenu |
|---|---|---|---|
| [01 — Financier](01-FINANCIER.md) | 14 | 3 | Coûts, PnL, sizing, métriques, enveloppes, Monte-Carlo |
| [02 — Backtest](02-BACKTEST.md) | 14 | 2 | Exécution, gaps, walk-forward, IS/OOS, parité |
| [03 — Optimiseur](03-OPTIMISEUR.md) | 13 | 3 | Sélection, gates, biais de recherche, gel de paramètres |
| [04 — Live](04-LIVE.md) | 16 | 2 | Ouverture, gestion, clôture, reprise, concurrence |
| [05 — Backend API](05-BACKEND-API.md) | 13 | 0 | Routes, transactions, exchange, rate limiting |
| [06 — Frontend UI/UX](06-FRONTEND-UI-UX.md) | 12 | 0 | États, sondage, typage, accessibilité, bundle |
| [07 — ML](07-ML.md) | 9 | 0 | Fuites, calibration, registre, gate de promotion |
| [08 — Performance](08-PERFORMANCE.md) | 10 | 0 | Points chauds CPU, appels réseau, mémoire |
| [09 — Sécurité](09-SECURITE.md) | 9 | 0 | Auth, secrets, exposition, dépendances, Docker |
| [10 — Tests & qualité](10-TESTS-QUALITE.md) | 7 | 0 | Couverture, nature des assertions, CI |
| [11 — Données](11-DONNEES.md) | 7 | 0 | Cache OHLCV, features, intégrité, cycle de vie |
| [12 — Architecture & dette](12-ARCHITECTURE-DETTE.md) | 7 | 0 | Couplage, duplication, code mort, conventions |
| [**13 — Révision 17/08**](13-REVISION-2026-08-17.md) | +4 | 0 | Delta des PR #222 à #228 : 5 résolus, 4 nouveaux, 8 critiques revérifiés |

---

## 8. Plan de travail suggéré

**Sprint 1 — arrêter de mentir aux chiffres** *(≈ 3 jours)*
Corrections 1, 2, 4, 5 et 9 du tableau §3. Chacune est locale, à faible risque,
et rend immédiatement les tableaux de bord lisibles. Ajouter en parallèle les
six assertions de définition listées en §6c.

**Ajout du 17 août — à faire avant le sprint 3** *(≈ 1/2 journée)*
**N-01** et **N-02** : le holdout livré par la PR #222 est du bon travail qui
ne protège pas encore le chemin que l'utilisateur emprunte. `required_total_bars`
ignore les 20 % réservés, donc le repli sans holdout se déclenche trop souvent ;
et le bouton « Appliquer » de l'UI décide toujours sur la tranche de sélection
alors que la donnée du holdout est déjà dans la fiche de job. Une dizaine de
lignes chacun pour que le bénéfice du sprint 3 soit réellement acquis. Voir
[la révision](13-REVISION-2026-08-17.md) §5.

**Sprint 2 — rétablir la comptabilité** *(≈ 1 semaine)*
Correction 3 (`net_profit` comme référence) puis 8 (emprunt à levier 1). Livrer
séparément, avec `schema_version` + `git_commit` sur les fichiers de résultats
(D-06) et purge de l'historique produit par l'ancien calcul.

**Sprint 3 — rétablir l'out-of-sample** *(≈ 1 semaine)*
Correction 7 (train / validation / test), purge et embargo entre tranches
(B-08, M-02), `n_trials` réel dans le Deflated Sharpe (O-07), et bascule sur
l'implémentation conforme (F-07).

**Sprint 4 — aligner exécution backtest / paper / live** *(≈ 1 semaine)*
Corrections 6 et 10, activation de `realistic_risk` sur les chemins de décision
(B-07), simulation du stop exchange en paper (L-02).

**En continu, dès maintenant** : faire tourner le bot en paper pour que
`oos_tracker` produise ses premiers verdicts (§6a). Sans cela, les quatre
sprints ci-dessus restent des corrections raisonnées mais non validées.
