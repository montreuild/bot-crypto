# Plan directeur — bot-crypto

> **À quoi sert ce document.** Il répond à une seule question : *qu'est-ce qui
> reste à faire, et pourquoi ça n'est pas encore fait ?* Tout le reste — ce qui
> a été livré, les mesures, les décisions écartées — n'est là que pour éviter de
> rouvrir un débat déjà tranché.
>
> **Comment il est tenu.** Un item ne passe à « fait » que si le CODE le
> montre, pas si un commit l'annonce. Cette règle n'est pas de la méfiance
> gratuite : ce plan a déjà affirmé que la calibration isotone était désactivée
> en 1 h pendant des semaines alors que `omnibus_v4_multi.yaml` portait
> `calibrate: true` (ML-11), et annoncé « 116 titres sur 117 inaptes au modèle
> solo » sur un cache incomplet (ML-17). Les deux ont été corrigés en
> re-mesurant, pas en relisant.
>
> **Refonte du 2026-07-28.** La version précédente avait accumulé six mois de
> `bis` et de `ter` : deux sections numérotées `3.7ter`, quatre sections `4.x`
> dupliquées entre les chapitres 4 et 5, et 69 items terminés mêlés aux items
> ouverts. Le backlog et le journal sont désormais séparés, et un index unique
> donne l'état de chaque identifiant.

---

## Table des matières

1. [Santé du dépôt](#1-santé-du-dépôt)
2. [Backlog — ce qui reste](#2-backlog--ce-qui-reste)
   - [2.1 Sécurité — le seul bloc à traiter en priorité](#21-sécurité--le-seul-bloc-à-traiter-en-priorité)
   - [2.2 ML & recherche](#22-ml--recherche)
   - [2.3 Multi-actifs — G3 et au-delà](#23-multi-actifs--g3-et-au-delà)
   - [2.4 Observabilité](#24-observabilité)
   - [2.5 Tests, docs, DX](#25-tests-docs-dx)
   - [2.6 Itératif — sans urgence](#26-itératif--sans-urgence)
3. [Index des items](#3-index-des-items)
4. [Journal — ce qui a été livré](#4-journal--ce-qui-a-été-livré)
5. [Décisions produit en attente](#5-décisions-produit-en-attente)
6. [Déclinés / obsolètes](#6-déclinés--obsolètes)

---

## 1. Santé du dépôt

| Indicateur | État | Détail |
|---|---|---|
| **CI** | 🟢 verte | `lint` (ruff 0.15.8) + `test` (pytest `-m "not slow"`) |
| **Tests** | 🟢 ~1 364 verts | 3 skipped. Voir la note d'exécution ci-dessous |
| **Lint** | 🟢 `ruff check .` = 0 | — |
| **Sécurité désérialisation** | 🟢 close | Plus aucun `pickle`/`joblib` : format 100 % LightGBM natif (`.lgb` + `.meta.json`) |
| **Durcissement API/déploiement** | 🔴 **5 items ouverts** | SEC-002 à SEC-006, vérifiés présents dans le code au 2026-07-28 — cf. §2.1 |
| **Observabilité** | 🟡 en progrès | OBS-01/02 livrés (métriques + logs structurés). Grafana, alerting et tracing restent |
| **Couches** | 🟢 strictes | `core → engine → live → api → strategies`, 0 import circulaire |
| **Stratégies actives** | 15 `manual_active` | sur 42 fichiers `app/strategies/` |
| **Multi-actifs** | 🟢 G1+G2 livrés | 120 titres SBF 120 en paper, data-only. G3 (exécution réelle) non commencé |

### Note d'exécution — défauts d'environnement, pas de code

Trois échecs et erreurs sont **spécifiques à Windows** et n'apparaissent pas sur
le runner Linux. Les documenter évite de les rediagnostiquer à chaque session :

| Test | Cause | Portée |
|---|---|---|
| `test_funding_flow::test_no_ml_dependency` | `open()` sans `encoding` sur une console cp1252 | Windows seulement |
| `test_ml_routes` (3 erreurs) | `os.symlink` exige un privilège Windows | Windows seulement |
| `test_trade_aggregates::test_get_trades_since_filter` | `NotADirectoryError` | Windows seulement |
| `test_lgb_logging` | abort LightGBM à la fermeture de l'interpréteur | Windows ; oblige à `--ignore` ce fichier pour obtenir un signal local |

Aucun n'est lié aux évolutions récentes ; ils sont reproductibles sur du code
antérieur. Les corriger relève de TEST-12 (nouveau, §2.5).

---

## 2. Backlog — ce qui reste

Priorité : 🔴 à traiter · 🟠 utile à court terme · 🔵 structurant, sans urgence.
Effort : S (heures) · M (jours) · L (semaine+).

### 2.1 Sécurité — le seul bloc à traiter en priorité

**Vérifié dans le code le 2026-07-28** : ces cinq items sont bien ouverts, ce
ne sont pas des reliquats de documentation.

| ID | Item | Fichier | Effort |
|---|---|---|---|
| **SEC-002** 🔴 | Path traversal via `tf` non validé → whitelist de timeframes dans `CandleStore._path` + `resolve().is_relative_to` | `app/core/candle_store.py` | S |
| **SEC-003** 🔴 | `web.allow_insecure: true` committé par défaut → opt-in par flag CLI/env, jamais via YAML | `config.yaml` | S |
| **SEC-004** 🔴 | Retirer `"testclient"` de la whitelist localhost WebSocket | `app/api/routes/ws.py` | S |
| **SEC-005** 🔴 | Retirer `auth_basic off;` sur `/api/optimize/stream` | `deploy/nginx.conf` | S |
| **SEC-006** 🔴 | Docs OpenAPI (`/api/docs`, `/api/openapi.json`) non protégées → désactiver quand `ENV=prod` | `app/api/main.py` | S |

Cinq items S qui tiennent en une demi-journée. Ils traînent depuis l'audit
externe ; c'est le plus mauvais rapport effort/risque du backlog.

| ID | Item | Effort |
|---|---|---|
| **SEC-01** 🔵 | Rotation des secrets via vault (protocole `SecretProvider`) | M |
| **SEC-03** 🔵 | Validation Pydantic systématique des payloads API (`app/api/schemas.py` inexistant) | M |
| **SEC-06** 🔵 | Migrations SQLite via Alembic. **Nuance** : `_migrate_schema` idempotent existe déjà — ce serait un upgrade d'outillage, pas un manque | M |

### 2.2 ML & recherche

| ID | Item | Effort | Pourquoi |
|---|---|---|---|
| **ML-10** 🔴 | **Réoptimiser les seuils ADX** des YAML (`adx_min`, `adx_threshold`, `needs_adx_above`, `adx_len`) | M | Ces seuils ont été choisis face à un ADX qui valait 35 en moyenne ; ils s'appliquent à un ADX qui vaut 28. Rien n'est cassé, rien n'est réglé. **Seul item qui bloque la confiance dans les stratégies actives** |
| **ML-12** 🟠 | Instruire les scores négatifs sur la fenêtre de validation | M | 3 des 4 cibles mesurées perdent sur le dernier tiers de l'historique, sous les deux conventions d'ADX. Protocole réduit (40 essais, une paire) donc pas un verdict — mais leurs bons scores viennent d'IS/OOS |
| **ML-20** 🟠 | **`gate_by_tf` — holdout déclinable par timeframe** | S | Découvert en mesurant ML-17. En 15m/30m/4h, **aucun** titre actions n'atteint le seuil d'éligibilité, et le plafond est celui de Yahoo (60 j en 15m/30m ; 730 j en 1 h, dont le 4 h est ré-agrégé), pas le nôtre : 1 428 / 714 / 1 445 barres au mieux. Baisser `holdout_bars` globalement dégraderait le 1 h et le 1 d, qui ont dix fois plus d'historique. **Arbitrage à poser avant de coder** : 300 barres de holdout en 15 m valent ~9 séances — assez pour promouvoir un modèle ? |
| **ML-21** 🟠 | **Découpler la profondeur de cache visée du `total` par cycle** | M | Le backfill se déclenche sur `len(cache) < total`, où `total` est la demande du cycle courant (500) et non la profondeur dont les recettes ont besoin (2 000 pour la fenêtre minimale, 3 400 pour un modèle solo). Un cache à 1 000 barres est donc « suffisant » pour le scanner et très court pour l'entraînement — et rien ne le rattrape hors `backfill_equities.py` lancé à la main. Constaté le 2026-07-28 : `AC.PA/1d` est resté à 1 000 barres après réouverture alors que 15m et 4h se re-remplissaient |
| **ML-13** 🔵 | Mode `follow_setup` dans V11 | M | Dernier morceau de la fusion omnibus. Sa machine anti-whipsaw (confirmation K bougies, cooldown, hystérésis) est du **code**, pas des valeurs : mérite son propre tour de mesure |
| **ML-15** 🔵 | Rejouer la mesure de la dimension symbole | S | Débloqué : le store contient désormais des titres à ≥ 8 000 barres (`BN.PA` 9 684, `MF.PA` 9 500). La mesure de 2026-07 concluait qu'un modèle par symbole n'apporte rien sur crypto ; à rejouer sur actions |
| **ML-18** 🔵 | Variante recette de `window_sweep` | S | Le sweep reste piloté par la stratégie ; seul `train` a sa variante recette. C'est la raison pour laquelle la page « Modèles » propose encore des stratégies dans ce formulaire-là |
| **ML-19b** 🔵 | Basculer `ml_dynamic_threshold` sur le chemin recette | M | Reste dehors de ML-19 : contrairement à ce que supposait l'item, son `_train` n'a jamais été migré — il entraîne via son propre `_train_lgbm`. L'équivalence n'y est donc **pas** acquise, et la bascule serait un pari, pas une décision d'exploitation |
| **ML-01** 🔵 | Gating de promotion par walk-forward multi-fenêtres | M | `oos_score` sur un seul split : constaté sur `opus_omnibus_v8_no_ml`/`v10_no_ml` un `oos_score` de production positif (0,76-0,77) mais un backtest fenêtre complète nettement négatif (PnL −95/−110) |

### 2.3 Multi-actifs — G3 et au-delà

**G1** (abstractions) et **G2** (SBF 120 en paper, data-only) sont livrés — cf.
§4. Ce qui suit reste ouvert.

| ID | Item | Effort | Détail |
|---|---|---|---|
| **G3** 🟠 | **Exécution réelle actions** | L | Aujourd'hui `can_execute: false` : le bot émet une notification de trade et suit la position comme en paper, aucun ordre n'est transmis. Suppose un courtier avec API (IBKR, Saxo, Degiro non documenté), la gestion du carnet et des horaires de séance, et le rapprochement des frais réels (courtage + TTF) |
| **G4** 🔵 | **Univers Nasdaq et ETF mondiaux** | M | *(nouveau, 2026-07-28)* — voir ci-dessous |

#### G4 — récupération des actions Nasdaq et des ETF mondiaux

**L'intention.** Étendre l'univers au-delà d'Euronext : valeurs Nasdaq
(AAPL, MSFT, NVDA…) et ETF mondiaux (indiciels larges, sectoriels, obligataires).
L'intérêt n'est pas seulement d'avoir plus d'instruments — c'est que le pooling
multi-symboles (ML-16) devient bien plus riche quand les titres poolés ne
partagent pas tous le même régime macro qu'un unique indice national.

**Ce qui marche déjà.** `yfinance` sert les tickers américains sans travail
supplémentaire, `exchange_calendars` connaît XNYS et XNAS, `Instrument` et
`VenueRegistry` sont neutres en devise, et un univers se déclare par un simple
fichier `data/universe/<nom>.yaml`. L'ajout d'un symbole depuis l'UI existe
depuis le 2026-07-28.

**Les quatre obstacles réels, vérifiés dans le code.**

1. **`suffix` est global au provider, pas à la venue.** `config.yaml` déclare
   `providers.yfinance.suffix: .PA`, et `YFinanceProvider.provider_symbol()`
   l'applique à tout ticker sans point. `AAPL` deviendrait donc `AAPL.PA` —
   une ligne parisienne inexistante, dont la réponse vide est indiscernable
   d'une suspension de cotation. C'est le blocage principal. Deux issues :
   déclarer plusieurs instances de provider (`yfinance-eu`, `yfinance-us`)
   indexées par venue, ou renseigner `provider_symbol` pour chaque membre —
   ce que le format d'univers accepte déjà, mais qui alourdit un fichier de
   plusieurs centaines de lignes.
2. **`session_hours: 8.5` est une constante Euronext.** Le NYSE cote 6,5 h.
   Cette valeur alimente `bars_span_ms`, donc la fenêtre calendaire du
   backfill : la laisser telle quelle ferait viser trop court sur les titres
   américains, et le cache n'atteindrait jamais la profondeur demandée — le
   défaut exact que `bars_span_ms` avait été introduit pour corriger.
3. **La devise n'est plus celle du capital.** Un univers USD adossé à un
   capital EUR introduit une exposition de change non modélisée. `venues.defs`
   porte déjà `quote_currency`, mais rien ne convertit le PnL ni ne dimensionne
   la position en tenant compte du taux. À trancher : couvrir, ignorer et
   documenter, ou refuser le multi-devises (**FIN-09** traite déjà ce sujet et
   devient un prérequis de G4).
4. **Le plafond de rétention Yahoo s'applique à l'identique.** 60 jours en
   15m/30m, 730 jours en 1 h. Les ETF récents et les titres récemment
   introduits seront donc inéligibles au pooling dans les mêmes conditions
   qu'en 15m/4h sur Euronext — cf. ML-20.

**Spécifique aux ETF.** Trois points que les actions n'ont pas : les
distributions (un ETF distribuant décroche du prix à chaque détachement, ce que
l'OHLCV brut ne signale pas), les ETF **synthétiques** dont le sous-jacent est
un swap (le volume coté ne reflète pas la liquidité réelle), et les
capitalisants dont le rendement total n'est pas dans la série de prix. Le
labelleur travaille sur des rendements : un détachement non ajusté produit un
faux signal de baisse. À instruire avant d'entraîner quoi que ce soit dessus.

**Découpage suggéré** : G4a = un univers Nasdaq restreint (~30 titres liquides)
avec provider par venue, pour lever l'obstacle 1 et 2 sans traiter le change ;
G4b = les ETF, après arbitrage sur les distributions ; G4c = le multi-devises,
qui dépend de FIN-09.

### 2.4 Observabilité

OBS-01 (métriques Prometheus) et OBS-02 (logs JSON + correlation IDs) sont
livrés — cf. §4.

| ID | Item | Effort | Dépend de |
|---|---|---|---|
| **OBS-03** 🔵 | Dashboard Grafana | M | OBS-01 |
| **OBS-04** 🔵 | Alertes Prometheus AlertManager | M | OBS-01 |
| **OBS-06/07** 🔵 | `/health` enrichi + alerting sur seuils critiques | M | — |
| **OBS-05** 🔵 | Tracing OpenTelemetry | L | backlog |

### 2.5 Tests, docs, DX

| ID | Item | Effort |
|---|---|---|
| **TEST-12** 🟠 *(nouveau)* | Corriger les 4 défauts de test spécifiques à Windows recensés au §1 — `encoding` explicite, symlinks conditionnels, abort LightGBM. Ils obligent aujourd'hui à ignorer un fichier entier pour obtenir un signal local | S |
| **TEST-04** 🟠 | `pytest-cov` + seuil de couverture en CI ; ajouter mypy et un scan de sécurité | M |
| **TEST-11** 🟡 | Smoke tests paramétrés pour les stratégies survivantes | M |
| **TEST-05** 🔵 | Tests d'ordres live mockés complets (idempotence `clientOrderId`, partial fills, réconciliation des frais, restauration après crash) | L |
| **TEST-06** 🔵 | Fixtures de non-régression backtest byte-identique. Partiellement couvert par `test_execution_parity.py` | M |
| **DX-01** 🟡 | `Dockerfile` + `docker-compose` (absents, vérifié) | M |
| **DOC-005/006** 🟡 | 4 guides référencés au README mais inexistants ; 0 ADR | M |
| **STRAT-01** 🔵 | Champ `status: experimental\|validated\|production\|archived` dans chaque YAML de stratégie — distinct du `SlotLifecycleManager` runtime | M |
| **ARCH-05** 🔵 | Réduire `app/strategies/smart_money.py` (838 L) et `smart_money_signals.py` (891 L). Dette de lisibilité, pas d'urgence | M |
| **UI-02** 🔵 | Refonte navigation 3 sections + `/onboarding` | M |
| **WKFLOW-01/02/03** 🔵 | Conventional commits, pre-commit, templates issues/PR | S |

### 2.6 Itératif — sans urgence

- **FIN-01** (frais VIP OKX dynamiques), **FIN-02** (borrow rate dynamique),
  **FIN-03** (reporting fiscal FIFO, dépend SEC-06), **FIN-05**
  (Sharpe/Sortino/Calmar/VaR/CVaR temps réel), **FIN-08** (réconciliation PnL
  quotidienne), **FIN-09** (multi-devises — **devient un prérequis de G4c**).
- **DX-02** (setup interactif), **DX-03** (hot-reload dev), **DX-04** (ADR),
  **DX-05** (profiling intégré), **DX-06** (OpenAPI enrichi).
- **PERF-03** (PostgreSQL), **PERF-04** (streaming SSE des backtests).
- **LIFE-01** (tests transitions cycle de vie), **LIFE-02** (timeline UI),
  **LIFE-03** (auto-re-optimisation), **LIFE-04** (allocation graduelle).
- **RES-01** (détection de régime HMM), **RES-02** (backtest portefeuille
  multi-actifs), **RES-03** (sentiment F&G), **RES-04** (extension de l'usage
  des dérivés).
- **ARCH-03/12** (`AppState` dataclass) — l'audit avait conclu que
  l'encapsulation actuelle suffit ; à ne faire que sur besoin concret.

---

## 3. Index des items

État : ✅ livré · 🔴🟠🟡🔵 ouvert (priorité) · ⛔ décliné.

| ID | État | Où |
|---|---|---|
| ARCH-01 (`setup_router`) | ✅ | §4 — absorbé par `opus_omnibus_v11` + `MLBackendMixin` |
| ARCH-03 / ARCH-12 | 🔵 | §2.6 |
| ARCH-05 | 🔵 | §2.5 |
| ARCH-07 | ✅ | §4 |
| BT-05 / STRAT-03 | ✅ | §4 |
| BT-11 | ⛔ | §6 |
| BT-13 / STRAT-06 | ✅ | §4 |
| DEAD-01 | ✅ | §4 — clos par factorisation |
| DEAD-03 | ✅ | §4 — **décision inversée** le 2026-07-28 |
| DOC-005/006 | 🟡 | §2.5 |
| DX-01 | 🟡 | §2.5 |
| DX-02…06 | 🔵 | §2.6 |
| FIN-01, FIN-02, FIN-03, FIN-05, FIN-08, FIN-09 | 🔵 | §2.6 |
| FIN-04, FIN-06, FIN-07 | ✅ | §4 |
| **G1**, **G2** | ✅ | §4 |
| **G3** | 🟠 | §2.3 |
| **G4** (Nasdaq + ETF) | 🔵 | §2.3 — **nouveau** |
| LIFE-01…04 | 🔵 | §2.6 |
| ML-01 | 🔵 | §2.2 |
| ML-02 | ✅ | §4 — registre, provenance, gate |
| **ML-10** | 🔴 | §2.2 |
| ML-11 | ✅ | §4 — `hp_by_tf` |
| ML-12 | 🟠 | §2.2 |
| ML-13 | 🔵 | §2.2 |
| ML-14 | ✅ | §4 — entraînement piloté par la recette |
| ML-15 | 🔵 | §2.2 — débloqué |
| ML-16 | ✅ | §4 — pooling câblé API + UI |
| ML-17 | ✅ | §4 — mesuré sur actions réelles |
| ML-18 | 🔵 | §2.2 |
| ML-19 | ✅ | §4 — `stat48_v4`/`v5` seulement |
| **ML-19b** | 🔵 | §2.2 — `ml_dynamic_threshold` reste dehors |
| **ML-20** | 🟠 | §2.2 — **nouveau** (2026-07-28) |
| **ML-21** | 🟠 | §2.2 — **nouveau** (2026-07-28) |
| OBS-01, OBS-02 | ✅ | §4 |
| OBS-03, OBS-04, OBS-05, OBS-06/07 | 🔵 | §2.4 |
| PERF-01, PERF-02 | ✅ | §4 |
| PERF-03, PERF-04 | 🔵 | §2.6 |
| RES-01…04 | 🔵 | §2.6 |
| SEC-001 (HMAC `.pkl`) | ⛔ | §6 — sans objet |
| **SEC-002 → SEC-006** | 🔴 | §2.1 |
| SEC-007/008, SEC-009/010 | ✅ | §4 |
| SEC-01, SEC-03, SEC-06 | 🔵 | §2.1 |
| SEC-04, SEC-05, SEC-020 | ✅ | §4 |
| STRAT-01 | 🔵 | §2.5 |
| STRAT-02 | ✅ | §4 — registre daté et versionné |
| TEST-01/02/03 | ✅ | §4 |
| TEST-04 | 🟠 | §2.5 |
| TEST-05, TEST-06, TEST-11 | 🟡/🔵 | §2.5 |
| **TEST-12** | 🟠 | §2.5 — **nouveau** |
| UI-02 | 🔵 | §2.5 |
| WKFLOW-01/02/03 | 🔵 | §2.5 |

---

## 4. Journal — ce qui a été livré

Antéchronologique. Seules les livraisons dont la conclusion **change une
décision future** sont détaillées ; les autres tiennent en une ligne.

### 2026-07-28 — CandleStore : les trous intérieurs et un log qui mentait

Deux défauts trouvés en instruisant des logs de réouverture de marché.

**Les trous intérieurs n'étaient comblés par aucun chemin.** Le fetch
incrémental ne regarde qu'après la dernière barre connue ; le backfill
historique ne gardait que ce qui précède la première. Entre les deux, un trou
restait un trou pour toujours — même quand la source publiait les barres
manquantes dans la même réponse, aussitôt jetées. Mesuré sur `AC.PA/4h` : la
source annonçait 1 522 bougies, le cache en stockait 1 442, l'écart de 80 ne
bougeait plus. Après correctif : **1 523 barres**.

**Le log annonçait une chose et en faisait une autre** — « tentative de
récupération de 126 bougies » puis « +1054 » : le chemin profond ignore la
borne `missing`. Le compte-rendu donne désormais le delta réel après
déduplication.

Livré aussi : gestion de l'univers d'instruments depuis la page Données (ajout,
retrait, profondeur de cache par TF), pooling choisi **par univers** au lieu
d'une saisie de 120 mnémoniques, comparaison solo vs poolé par titre dans l'UI,
liste d'entraînement réduite aux recettes.

> **Leçon d'outillage, à ne pas réapprendre.** `gh pr merge` fait un
> `checkout` + `pull` local. Des fichiers qui passent de suivis à ignorés sont
> donc **supprimés du disque** au fast-forward. C'est arrivé aux 79 parquets de
> cache, restaurés ensuite depuis git — donc dans leur version d'avant backfill,
> ce qui a provoqué une matinée de re-téléchargement et des logs alarmants.
> Avant de fusionner une PR qui dé-versionne des données : sauvegarder hors
> git, ou vérifier le contenu avant de restaurer.

### 2026-07-27/28 — Lot de huit items du plan

**ML-11 — calibration isotone désactivée en 1 h.** Sous-décision tranchée : un
bloc **`hp_by_tf:`** par TF, pas une recette par TF. Sa précédence est
volontairement **inversée** (il l'emporte sur les `params` reçus), pour une
raison mécanique : chaque stratégie recopie `hp` dans ses `_DEFAULTS` et ses
`fixed_params`, valeurs sans timeframe qui arrivent donc toujours dans `params`.
Traité comme un simple défaut, le bloc n'aurait jamais été appliqué — on aurait
écrit un réglage inerte, exactement l'écart doc/code qu'on corrigeait.

**ML-16 — pooling câblé.** `train_multi` était testé et appelé par personne.
Holdout prélevé **par symbole avant** l'entraînement, gate sur la moyenne **non
pondérée** — pondérer par le nombre de barres laisserait le titre au plus long
historique décider seul, ce que le pooling cherche à éviter.

**ML-17 — pooling mesuré sur actions réelles** (`scripts/measure_pooling_equities.py`,
cache local, sans réseau). L'obstacle d'egress supposé n'existait plus.

| | |
|---|---:|
| Titres `.PA` en cache (1 d) | 120 |
| Entraînables **seuls** (≥ 3 400 barres) | 103 |
| Dépendants du pooling | **17** |

| Modèle poolé (16 titres) | AUC amp | AUC dir |
|---|---:|---:|
| Validation interne | 0,685 | 0,509 |
| **Holdout jamais vu** | **0,630** | 0,500 |

Solo vs poolé sur 8 titres : 6 où le pooling aide, 1 où il coûte, 1 équivalent,
écart moyen **+0,016**. Ces huit titres étant précisément ceux qui ont assez
d'historique pour s'en passer, ce que le chiffre établit n'est **pas un gain**
mais l'absence de dégradation — ce qui autorise à servir tout l'univers avec un
modèle unique au lieu de maintenir deux régimes. La direction reste à 0,500,
soit exactement le hasard : une raison de plus de ne pas balayer les seuils
`dir_*`.

> Une première passe (2026-07-27) concluait « 116 titres sur 117 inaptes au
> modèle solo ». Ce chiffre décrivait un cache incomplet, pas une propriété du
> marché : `backfill_equities.py` a tourné entre les deux mesures et la médiane
> journalière est passée de ~1 000 à 6 824 barres.

**ML-19 — artefacts `stat48_*` promouvables.** Le point restant n'était pas
`fit()`/`score()` mais l'**écriture** : `save_lgb_with_scaler` produit un
meta.json sans features ni médianes, cause exacte de l'`unsupported_format` qui
faisait conclure « comparaison manuelle requise » au gate quoi qu'il arrive.
Mesuré avant/après : `{'unsupported_format': True}` → `{'auc_amp': …}`.

**OBS-01 — métriques Prometheus.** Les métriques métier sont dérivées de
`EventHub.publish`, point de branchement unique, plutôt que semées dans le
trader. Cardinalité protégée : libellé par **template** de route, jamais par URL.

**OBS-02 — logs JSON + correlation IDs.** JSON Lines sur le handler fichier,
console inchangée. Un `ContextVar` ne traverse pas un thread : les jobs de fond
transportent l'identifiant explicitement. Les ~900 f-strings ne sont **pas**
réécrites. Au passage, `_ColorFormatter` mutait `record.levelname` : les
séquences ANSI finissaient dans `bot.log`.

**SEC-007/008** — l'alerte de crash ne transmet plus le log par défaut ; le
filtre existant ne masquait que ce qui *ressemble* à un secret, jamais les
données d'exploitation. Sauvegardes en `600`/`700`.
**SEC-009/010** — `jinja2` 3.1.6 (et non 3.1.5, elle-même vulnérable),
`sqlalchemy` 2.0.32.

**Hygiène du dépôt** — caches `data/ohlcv` et `data/derivatives` et artefacts
du registre ML sortis de git, **sauf `models/_archive/`** : ces 9 fichiers
viennent d'un script hors dépôt et sont impossibles à régénérer, git est leur
seule protection. `starlette==0.38.6` épinglé après une dérive en 1.3.1 qui
cassait toute la collecte des tests API. `gate.holdout_bars` 1500 → 1400.

### 2026-07-26/27 — ML-14, G2, DEAD-01

**ML-14 — entraînement piloté par la recette.** `train(recipe, df, tf)`
n'importe aucune stratégie, verrouillé par un test qui fait échouer tout import
de `app.strategies.*` pendant l'entraînement. Équivalences mesurées avant
bascule : écart max **0.00000000** face à `MLBackend`, **0 divergence de
signal** sur 10 fenêtres. Au passage : `stat48` a **56 colonnes, pas 48**.

**G2 — actions SBF 120 en paper**, data-only (`can_execute: false`).
**DEAD-01** clos par factorisation plutôt que par suppression.
**ML-02** — registre daté et versionné, provenance complète (hash de recette,
commit git, dates, symbole d'entraînement).

### Antérieur (résumé)

Phases 1-6 de refactoring structurel · remise au vert de la CI · persistance
native LightGBM (fin des `.pkl`) · **PERF-02** parallélisme réel de
l'optimiseur · **SEC-04** rate-limiting granulaire · **SEC-05** backup
automatique · **FIN-04/06/07** · **BT-05**, **BT-13** · **ARCH-07** ·
**TEST-01/02/03** · **STRAT-02** · **PERF-01**.

---

## 5. Décisions produit en attente

- **ML-20 — quel holdout par timeframe ?** 300 barres en 15 m valent ~9
  séances. Assez pour promouvoir un modèle, ou faut-il renoncer à promouvoir
  automatiquement sur les TF intraday actions ? Rien ne peut avancer sans cet
  arbitrage.
- **G4 — le multi-devises est-il dans le périmètre ?** Un univers Nasdaq en USD
  adossé à un capital EUR crée une exposition de change. Couvrir, ignorer et
  documenter, ou refuser ? Détermine si G4c existe.
- **G4 — les ETF distribuants sont-ils acceptés ?** Sans ajustement des
  détachements, le labelleur lira un faux signal de baisse à chaque
  distribution.
- **Univers SBF 120** : la composition porte `verified: true` au 2026-07-26,
  mais l'indice est révisé trimestriellement — à recontrôler
  (`scripts/check_universe.py sbf120`). Le taux de TTF retenu (0,4 %) reste à
  confirmer.
- **FIN-01** : implémenter les frais VIP OKX dynamiques.

---

## 6. Déclinés / obsolètes

Ne pas rouvrir sans confirmation explicite.

- ⛔ **BT-11 / plafond d'exposition BTC+ETH corrélés** — décliné par
  l'utilisateur (multi-crypto corrélé assumé).
- ⛔ **HMAC de signature des `.pkl`** (ancien SEC-001) — sans objet : il n'y a
  plus aucun `.pkl` ni pickle dans le code.
- ⛔ **Encapsulation `AppState` complète** — optionnelle, aucune inversion de
  couche restante ; à ne faire que sur besoin concret (multi-instances).
- ✅ **DEAD-03 / dé-versionner `data/`** — **décision inversée le 2026-07-28**.
  L'entrée précédente disait « les parquets sont poussés volontairement, ne pas
  exécuter sans accord explicite » ; cet accord a été donné. Les caches
  `data/ohlcv` et `data/derivatives` sont désormais ignorés et détachés, les
  fichiers restant sur disque. Restent suivis à dessein : `data/universe/`,
  `data/oos_tracker.json`, `data/backtest_history.json` — écrits par décision,
  pas par accumulation. **Conséquence** : un clone neuf démarre avec un cache
  vide, à amorcer par `scripts/backfill_equities.py`.
