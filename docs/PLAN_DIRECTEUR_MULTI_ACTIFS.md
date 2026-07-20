# Plan Directeur — Généralisation Multi-Actifs (Crypto + Actions SBF120) & Améliorations

> **Document de référence unique**, fusion de **trois sources** vérifiées contre le
> code réel au commit `HEAD` (branche `claude/generic-bot-crypto-stocks-6t7lsc`,
> **2026-07-18**) :
> 1. **Audit initial** (`docs/audit/00-INDEX.md` + 7 fichiers domaine + `mesures-vague5.md`)
>    — 89 items, daté 2026-07-11, réalisé en 6 « vagues » entre le 11 et le 13/07.
> 2. **Plan complémentaire** (upload utilisateur, ci-après « Plan C ») — ~90 items
>    en 12 EPICs, daté 2026-07-14 (post V12.17.0), analyse statique du repo.
> 3. **Ce document** (Plan Directeur) — généralisation multi-actifs + 26 items de
>    correctifs vérifiés, Sprints 0/1/2/4 réalisés le 2026-07-17.
>
> Chaque item ci-dessous est classé **FAIT** (preuve : commit ou grep/wc -l réel),
> **OBSOLÈTE/DÉCLINÉ** (prémisse fausse ou décision utilisateur), ou **RESTE À
> FAIRE** (reformulé, dédupliqué entre les 3 sources).
>
> **Mise à jour 2026-07-20** (branche `claude/sprint-7-8-planning-xb12m0`,
> commits `5022b69`…`3bbcd55`) : voir `CHANGELOG.md` § « Post-Sprint 8 » pour
> le détail technique. Résumé des changements de statut apportés par cette
> passe : **PERF-02** (Plan C, §4.5) marqué **FAIT** (parallélisme réel de
> l'optimiseur + refonte `param_search_optim`, détail §4.1bis) ; **TEST-01**
> nuancé — le job CI `lint` existait depuis Sprint 7 mais n'était **jamais
> passé** (773 erreurs pré-existantes, aucune liée à un sprint de ce
> document) jusqu'à cette passe, désormais vert ; **DEAD-01** toujours
> ouvert mais son **analyse est maintenant livrée et re-confirmée** (deux
> passes de comparatif fonctionnel + empirique des 15 variantes opus_omnibus/
> opus_stat sur 5 timeframes — la seconde avec un dimensionnement de fenêtre
> calqué sur la production et sans aucune stratégie sautée, rapports HTML
> remis à l'utilisateur) — la décision de suppression elle-même reste en
> attente, aucun fichier supprimé. Découverte annexe hors périmètre DEAD-01 :
> `v11`/`v12` (actives) sous-performent sur leur TF de production (1h) dans
> ce test, signalé pour examen séparé (§4.1). Garde-fou ajouté : `/api/
> backtest` et `/api/optimize/start` se refusent désormais mutuellement
> pendant que l'autre tourne (contention CPU/mémoire constatée en pratique).

---

## Table des matières

0. [Note d'exécution — flakiness pré-existante de la suite complète](#0-note-dexécution--flakiness-pré-existante-de-la-suite-complète)
1. [Bilan consolidé des 3 sources](#1-bilan-consolidé-des-3-sources)
2. [Architecture cible multi-actifs](#2-architecture-cible-multi-actifs)
3. [Chantier G — Généralisation (détail exécutable)](#3-chantier-g--généralisation-détail-exécutable)
4. [Ce qui reste à faire — backlog fusionné et priorisé](#4-ce-qui-reste-à-faire--backlog-fusionné-et-priorisé)
5. [Roadmap par sprints (mise à jour)](#5-roadmap-par-sprints-mise-à-jour)

---

## 0. Note d'exécution — flakiness pré-existante de la suite complète

En validant les Sprints 0-1, `pytest tests/` (610 tests à l'époque, 649 au
2026-07-18) s'est bloqué de façon non-déterministe (~1 run sur 3-4) lors de
l'exécution de la suite COMPLÈTE en un seul process — jamais en lançant les
fichiers individuellement ou par moitiés. Root-cause isolée par bisection :
**le blocage se reproduit à l'identique sur le code d'AVANT le Sprint 1**
(`git stash` → HEAD Sprint 0 seul, sans aucun des verrous `RLock` ajoutés) —
donc **non lié** aux correctifs `RiskManager`/`OHLCVCache`/`CapitalAllocator`
de ce plan. C'est une flakiness pré-existante de l'environnement sandboxé
(probable throttling CPU cgroup sous charge de threads multiples). Sans accès
à l'infra CI réelle, hors périmètre des sprints ; mitigé pragmatiquement en
réduisant la charge de `test_risk_thread_safety.py`. Recommandation : exécuter
`pytest` avec `pytest-timeout` en CI (cf. TEST-01 §4) pour transformer un
blocage en échec explicite plutôt qu'un run qui ne se termine jamais.

---

## 1. Bilan consolidé des 3 sources

### 1.1 Vue d'ensemble

| Source | Items | Réalisés | Restants | Détail |
|---|---:|---:|---:|---|
| **Audit initial** (`docs/audit/`, Vagues 0-6) | 89 | **~85** | ~4 | Vagues 0,1,2,4,5,6 ✅ complètes ; **Vague 3** (code mort + CI/lint) faite en Sprint 7 (commit `d19c978`) sauf **DEAD-01** (toujours ouvert, exclu explicitement) — TEST-11 reste bloqué par DEAD-01. |
| **Plan C** (upload, 12 EPICs) | ~90 | **~25** (dont 6 découvertes non documentées par le Plan C lui-même) | ~65 | §0.4 déjà livré (frontend rewrite) + 6 items déjà résolus (§1.3.1) + 9 items Sprint 8 (FIN-04/06/07, STRAT-06, SEC-04/05, ARCH-07 partiel, BT-05, PERF-01) + **PERF-02** (2026-07-20, §4.1bis), hors FIN-01 |
| **Plan Directeur** (ce document) | 26 (Sprints 0/1/2/4) + 3 vérifs ARCH + 18 (Sprints 7/8) | **47/47 sur ce lot** | Sprints 3/5/6 (généralisation actions + dette de fond) jamais démarrés ; DEAD-01/TEST-11/FIN-01 restent ouverts (exclus explicitement des Sprints 7/8) | Commits `ea9706e`/`7aaed6b`/`49be475`/`78ae183`/`d19c978`, 689 tests verts |

**Total approximatif** : sur ~205 items distincts recensés dans les 3 documents
(après dédoublonnage des ID réutilisés — cf. note ci-dessous), **~120 sont
faits** (dont **PERF-02**, ajouté 2026-07-20 — cf. §4.1bis), **~5 sont
déclinés/obsolètes par construction**, **~80 restent à faire** (détail
priorisé en §4).

> ⚠ **Piège de numérotation** : le Plan C réutilise les préfixes `ARCH-`,
> `TEST-`, `OPS-`, `UI-`, `SEC-` de l'audit initial pour des items **différents**
> (ex. Plan C `ARCH-01` = « extraire OpusBase », audit `ARCH-01` = « parité
> params live/backtest » — aucun rapport). Dans ce document, `ARCH-01 (audit)`
> et `ARCH-01 (Plan C)` désignent donc deux items distincts, désambiguïsés par
> la source entre parenthèses partout où c'est ambigu.

### 1.2 Audit initial (`docs/audit/`) — bilan par vague

Vérifié par `git log --oneline` : chaque item a un commit taggé `[ID] titre`
correspondant. Les 6 premières vagues sont **intégralement commitées** :

| Vague | Contenu | Statut | Commits (échantillon) |
|---|---|---|---|
| 0 | Régressions refonte per-symbole (BT-01, OPS-01, UI-02/03/04, BT-12) | ✅ | (pré-existant à ce plan) |
| 1 | Sécurité & intégrité (OPS-02/03/04/05/07, UI-01) | ✅ | `f7ec351`, watchdog service présent (`deploy/crypto-bot-watchdog.service`) |
| 2 | Intégrité de la mesure (BT-02/03/04/06/08/09, ARCH-01, BT-07) | ✅ | `f102572`, `1f4d047`, `d03275a`, `c89281b`, `bb7f759`, `009b036`, `8ba0c67` — **BT-05 seul non fait** (cf. §4) |
| 3 | Nettoyage & outillage (DEAD-01/02/03/05/06/07/09, TEST-01/04/05/06) | ❌ **jamais exécutée** | aucun commit `[DEAD-` ni `[TEST-01]`/`[TEST-04]`/`[TEST-05]`/`[TEST-06]` — vérifié : les 8 fichiers stratégies mortes (8005 lignes) existent toujours, `.github/workflows/` absent, `.flake8`/`mypy.ini`/`ruff.toml`/`pytest.ini` absents |
| 4 | Architecture (ARCH-02/03/04/05/06/07/08/09/10/11/12/13/14, OPS-08/09/10) | ✅ | `2197ff6`, `cc0007a`, `a834768`, `f717c87`, `ce75d94`, `fa4fa21`, `87ac2cd`, `845a325`, `ade3add`, `ab44207`, `04e4dd4`, `1176ae3` |
| 5 | Recherche edge SMC/ICT (SMC-01→15, BT-10, BT-11 décliné) | ✅ | `586124a`, `5227788`, `549e030`, `7897e72`, `bc3b7b7`, `d7c8ea5`, `0a18587`, `c047bc2` |
| 6 | UX/a11y/docs (UI-05→12, TEST-02/03/07/08/09/10/12) | ✅ (sauf TEST-11, bloqué par DEAD-01) | `9eab974`…`21e322d` |

**Vérifications indépendantes faites dans cette session** (pas seulement lecture
du doc) :
- `grep -rln "from app\.\(engine\|live\|api\)" app/core` → 0 ; `from app.api` dans
  `app/live` → 0 ; `from app.live` dans `app/strategies` → 0 (0 violation de couche, confirme ARCH-02/04/09 audit).
- `app/core/singleton.py::lazy_singleton` existe, utilisé par `candle_store.py`
  et `feature_store.py` (confirme ARCH-13 audit).
- `app/core/smc.py` = 65 lignes (façade), confirme ARCH-14 audit.
- `app/live/live_trader.py` = 484 lignes (confirme ARCH-06 audit).

**Reste de la Vague 3** (jamais faite) → détaillé en §4.1.

### 1.3 Plan complémentaire (Plan C, upload `ce6370b9…`) — corrections vérifiées

#### 1.3.1 Items que le Plan C liste comme ouverts mais qui sont **déjà faits**

Le Plan C a été généré par « analyse statique » le 2026-07-14, soit **après**
la fin de la Vague 4 de l'audit initial (13/07) — mais son analyse contient
des chiffres obsolètes ou faux sur plusieurs points d'architecture qui avaient
pourtant déjà été corrigés la veille. Vérifié dans cette session :

| Item Plan C | Prétention du Plan C | Constat réel (vérifié) | Verdict |
|---|---|---|---|
| **ARCH-02** (Plan C) | « `live_trader.py` (951 L) … Cible < 500 L » | `wc -l app/live/live_trader.py` → **484 lignes**, déjà scindé en mixins (`PositionMixin`, `BalanceSyncMixin`, `AutoOptMixin`, `HealthMixin`) depuis le commit `ab44207` [ARCH-06 audit] du 07-13 | ✅ **DÉJÀ FAIT** |
| **ARCH-06** (Plan C) | « Déplacer `_save_yaml` vers `core/yaml_io.py` » | Fait depuis `f717c87` [ARCH-04 audit] : `live_trader.py` utilise `app.core.yaml_io.update_config_yaml`, 0 référence à `app.api` (`grep -rn "from app.api" app/live` → vide) — **re-vérifié dans cette session, tâche #29** | ✅ **DÉJÀ FAIT** |
| **ARCH-07** (Plan C) | « 25 littéraux `"BTC/USDC"`, cible ≤ 3 hors tests » | Fait en grande partie depuis `fa4fa21` [ARCH-10+11 audit] (`DEFAULT_CONFIG_SYMBOL` exporté et utilisé) mais `grep -rn '"BTC/USDC"' app/ \| grep -v test \| grep -v DEFAULT_CONFIG_SYMBOL` → **16 occurrences résiduelles** (cible ≤3 non atteinte) | 🟡 **PARTIEL** — reste un nettoyage S (cf. §4.3) |
| **ARCH-08** (Plan C) | « Factoriser `_lazy_singleton` » | Fait depuis `ade3add` [ARCH-13 audit] : `app/core/singleton.py::lazy_singleton()` existe, `DATA_ROOT` centralisé | ✅ **DÉJÀ FAIT** |
| **ARCH-04** (Plan C) | « 144 imports scopés `from app.` dans des fonctions, symptôme d'inversion de couche » | **Déjà vérifié faux dans cette session** (tâche #27, avant lecture du Plan C) : compte réel **56** (grep hors `__pycache__`), **0 violation de couche** (core→engine/live/api, live→api, live→strategies tous à 0). Les scoped imports restants sont dans `app/api/*` (couche autorisée à tout importer) ou du lazy-loading intra-couche légitime | ❌ **PRÉMISSE FAUSSE — refactor aveugle sans gain, ne pas exécuter** |
| **ARCH-05** (Plan C) | « `smc.py` (1083 L) et `smart_money.py` (1178 L), cible < 450 L chacun » | `smc.py` = 65 L (façade, ✅) mais `smart_money.py` = **836 L** et `smart_money_signals.py` = **702 L** — le découpage a eu lieu (ARCH-14 audit) mais aucun des deux fichiers résultants n'est sous 450 L | 🟡 **PARTIEL** — le split existe, la cible de taille non atteinte (cf. §4.3, effort réduit car structure déjà en place) |
| **STRAT-04** (Plan C) | « Modèle de slippage dépendant de la taille, absent » | **Déjà fait** : `backtest.slippage_model` (`"static"` par défaut, `"size"` opt-in) câblé dans `app/engine/backtest.py:241,770,921`, mesuré et documenté dans `mesures-vague5.md` [BT-10 audit] | ✅ **DÉJÀ FAIT** (off par défaut, comme spécifié) |
| **STRAT-07** (Plan C) | « Walk-forward jamais branché sur l'auto-apply — à vérifier si BT-07 l'a fait » | Le Plan C soupçonnait lui-même que BT-07 avait pu le faire. Vérifié : `optimizer.wf_gate` (défaut `True`) est lu dans `auto_optimizer.py:522`, gate complet avec logs de refus | ✅ **DÉJÀ FAIT** — le Plan C avait raison de douter, confirmation apportée ici |

**Six items** que le Plan C présentait comme du travail restant sont donc en
réalité déjà terminés (quatre entièrement, deux partiellement) — le Plan C
n'a pas vérifié son hypothèse contre le code au moment de sa rédaction.

#### 1.3.2 Items obsolètes ou déclinés par décision utilisateur

| Item | Concerné | Raison |
|---|---|---|
| **BT-11** (audit) / **STRAT-05** (Plan C) — plafond d'exposition sur actifs corrélés (BTC+ETH) | Les deux décrivent le même garde-fou | **Décliné explicitement par l'utilisateur** lors de la Vague 5 (« ⛔ BT-11 exclu par décision utilisateur (multi-crypto corrélé assumé) », `00-INDEX.md` L137) — ne pas ré-ouvrir sans confirmation explicite |
| **DEAD-03** (audit) — dé-versionner `data/ohlcv`/`data/derivatives` de git | — | **Décision utilisateur requise**, explicitement bloqué (« ne pas dé-versionner sans son accord explicite » — les parquets sont poussés volontairement depuis la machine de l'utilisateur, l'environnement distant n'a pas accès aux exchanges). Reste ouvert **uniquement** le sort de `XRP_USDC` (données présentes, absent de `scanner.symbols`) |
| **1.3** (mon plan, §1.2 ancienne version) — validation scale-in cumulé | — | Déjà invalidé lors du Sprint 4 (S4-07), remplacé par un test de régression |
| **6.2** (mon plan) — rate-limiting exchange par token bucket maison | — | Requalifié : `enableRateLimit: True` ccxt suffit, dupliquer serait un sur-engineering |

### 1.4 Mon plan directeur — Sprints déjà réalisés

Sprints 0, 1, 2, 4 (26 items) **intégralement terminés et poussés** :
commits `ea9706e` (Sprint 0), `7aaed6b` (Sprint 1), `49be475` (Sprint 2),
`78ae183` (Sprint 4). 649 tests verts (33 nouveaux vs les 616 d'avant ces
sprints). Détail item par item : voir historique de ce document (git blame)
ou `CHANGELOG.md` (section `[Non publié]`). Les trois vérifications
ARCH-04/05/06 (numérotation Plan C, demandées explicitement par l'utilisateur)
sont documentées en §1.3.1 ci-dessus avec preuve de code — **aucune n'a
nécessité d'implémentation**, toutes obsolètes/déjà faites.

**Non démarré** : Sprint 3 (G2 — actions SBF120 en paper), Sprint 5 (G3 —
exécution réelle actions), Sprint 6 (fond de dette). Détail inchangé en §3-§5.

#### 1.4.1 Passe de re-vérification de la branche (2026-07-18)

Revue complète du diff `origin/main..HEAD` (50 fichiers, ~2 330 insertions)
demandée après coup, item par item :

- **Code : aucun correctif nécessaire.** Les 26 items relus dans le diff sont
  conformes à leur intention ; points sensibles re-validés à la lecture :
  chaîne d'auth cookie complète de bout en bout (cookie posé par `_tpl` →
  `verify_api_key` lit header OU cookie → `EventSource` du frontend passe
  bien `withCredentials: true` → WS lit le cookie en premier) ; échec de
  clôture d'ordre = position remise dans `open_positions` sous
  `_positions_lock` (pas de perte d'état allocateur, rien n'était encore
  désenregistré à ce point du flux) ; fetch réseau de `OHLCVCache.get()`
  hors verrou (pas de sérialisation inter-symboles) ; `df_is` bien en scope
  au call site de `_save_ml_model_post_opt` ; alias
  `min_volume_quote_24h` propagé AVANT le merge des défauts (une valeur
  utilisateur de l'ancienne clé n'est pas écrasée par le défaut générique).
- **Suite complète : 649/649 verts en 19,7 s** sur ce run (la flakiness §0
  ne s'est pas manifestée — elle reste non-déterministe, pas « corrigée »).
- **Documentation : 3 lacunes trouvées et corrigées** (les sprints avaient
  modifié le comportement sans mettre à jour les docs transverses) :
  1. `CHANGELOG.md` `[Non publié]` était vide → section complète ajoutée
     (Sprints 0/1/2/4 + fusion des plans), dans le style des versions
     précédentes.
  2. `ARCHITECTURE.md` ignorait `app/core/providers.py`, l'extension `Venue`
     (asset_class/quote_currency/…), `bars_per_year`, `live.trailing`, et
     décrivait l'auth API sans le cookie HttpOnly ni le strict-env → complété
     (liste des couches, « Sources uniques », section Sécurité).
  3. `README.md` § Configuration montrait des clés API en dur dans
     config.yaml alors que le fichier réel utilise `${OKX_API_KEY}`/
     `${WEB_API_KEY}` résolues depuis `.env` (généré par `scripts/setup.sh`),
     avec blocage strict en live → section réécrite.

---

## 2. Architecture cible multi-actifs

*(Section inchangée depuis la version précédente de ce document — toujours
valide, non impactée par la fusion des audits.)*

### 2.1 Ce qui est déjà générique (ne pas toucher)

- **Stratégies** : `BaseStrategy.analyze()` consomme des DataFrames OHLCV —
  aucune dépendance crypto dans l'interface.
- **Moteur/backtester/optimiseur/lifecycle** : tout est déjà indexé par slot
  `strategy::tf::symbol` (`bot_identity.py`). Le slot
  `pullback_trend::1d::AIR.PA` est représentable sans modification.
- **CandleStore** : stockage Parquet par `(symbol, tf)`, agnostique de la source.
- **Couplage ccxt confiné à 5 fichiers** : `core/exchange.py`,
  `core/candle_store.py`, `core/derivatives.py`, `live/position_mixin.py`,
  `api/routes/derivatives.py`.
- **Config `venues`** : la section existe déjà (`config.yaml:7-24`) avec
  `defs` + `assign`, étendue par S2-02 (`Instrument` + `VenueRegistry`,
  `app/core/providers.py`) — c'est le point d'extension naturel, **déjà posé**.

### 2.2 Les 4 points de couplage crypto à casser

**(a) Couche marché/exécution.** `MarketDataProvider`/`ExecutionProvider`
(Protocol) **déjà créés** en Sprint 2 (S2-01, `app/core/providers.py`).
`RobustExchange` les implémente de facto. Reste : un provider **data-only**
pour actions (§3, G2).

**(b) Modèle d'instrument et devise de cotation.** `Instrument`/`VenueRegistry`
**déjà créés** (S2-02, `app/core/instruments.py` via `providers.py`), devise
de cotation neutralisée (S2-03). Reste : brancher un vrai provider actions.

**(c) Calendrier de marché — le point le plus structurant, non démarré.**
La boucle live suppose toujours un marché 24/7. Reste intégralement à faire
(§3, G2/S3-02) : `MarketCalendar`, gating `_cycle()`/`CandleStore`, seuil de
gap par classe d'actif, mode `close_at_session_end`.

**(d) Coûts et sizing.** `compute_size` reste en fractions continues (pas de
`lot_size`/`tick_size`). Modèle de frais par venue (fixe + % + TTF française
0,4 %) non implémenté. Reste à faire (§3, G2/S3-03/S3-04).

### 2.3 Compatibilité des stratégies

`BaseStrategy.asset_classes` **déjà ajouté** (S2-04), `funding_flow`/
`derivatives_reversion` marquées `frozenset({"crypto"})`, filtre câblé dans
`_build_active_per_tf`. Fait.

### 2.4 Fournisseurs pour le SBF120

*(Inchangé — recommandations non encore mises en œuvre)*

| Rôle | Option recommandée | Alternative |
|------|--------------------|-------------|
| Données 1h/1d | `yfinance` (tickers `.PA`, gratuit) | EOD Historical Data (payant) |
| Exécution réelle | Interactive Brokers via `ib_insync` | Saxo OpenAPI |
| Calendrier | `exchange_calendars` (XPAR) | `pandas_market_calendars` |
| Liste SBF120 | `data/universe/sbf120.yaml` statique | Scraping Euronext (à éviter) |

---

## 3. Chantier G — Généralisation (détail exécutable)

*(Section inchangée — toujours le plan de référence pour la généralisation
actions. G1 est fait, G2/G3 non démarrés.)*

### G1 — Abstractions sans changement de comportement — ✅ FAIT (Sprint 2)

1. `app/core/providers.py` : protocoles `MarketDataProvider`/`ExecutionProvider` ✅
2. `Instrument` + `VenueRegistry` (extension `venues.defs`) ✅
3. Neutralisation devise de cotation (balances, scanner, `min_volume_quote_24h`) ✅
4. `asset_classes` sur `BaseStrategy` + marquage dérivés + filtre ✅
5. Golden test parité BTC/USDC (`tests/test_generic_parity.py`) ✅

### G2 — Actions en paper (Phase 2) — ❌ non démarré

1. `app/core/providers_equity.py::YFinanceProvider` → CandleStore Parquet.
2. `app/core/market_calendar.py` (wrapper `exchange_calendars`, XPAR) +
   gating `_cycle()`/`CandleStore`/forward-test, seuil de gap par classe
   d'actif, `close_at_session_end`.
3. Sizing entier (`lot_size`, floor) + stops au `tick_size`.
4. Frais par venue (fixe + % + TTF 0,4 %) partagés backtest/live.
5. `data/universe/sbf120.yaml` + scanner mode equity.
6. ML : réentraînement par instrument (fix `"BTC" in s` codé en dur dans
   `ml/trainer.py`).
7. Critère de sortie : backtest + optimisation + paper trading ≥3 valeurs
   SBF120 sur 1h/1d pendant une semaine, calendrier respecté, TTF visible.

### G3 — Exécution réelle actions (Phase 3) — ❌ non démarré

1. `IBKRExecutionProvider` (`ib_insync`) ou Saxo OpenAPI, idempotence `orderRef`.
2. `BalanceSyncMixin` multi-venue (EUR + USDC, équité globale convertie).
3. `check_correlation` par classe d'actif, `max_asset_class_exposure_pct`.
4. SRD/short actions : hors périmètre, chantier séparé.

---

## 4. Ce qui reste à faire — backlog fusionné et priorisé

Dédupliqué entre les 89 items de l'audit initial, les ~90 items du Plan C et
les Sprints 3/5/6 de ce document. Regroupé par thème, priorisé selon la
même grille Impact×Effort que le Plan C (🟢 quick win, 🔵 bet structurant,
🟡 itératif, 🟠 backlog).

### 4.1 🟢 Nettoyage & CI — Vague 3 de l'audit — ✅ FAIT (Sprint 7), sauf DEAD-01/TEST-11

Sprint 7 exécuté le 2026-07-18, **hors DEAD-01 et TEST-11** (exclus
explicitement par décision utilisateur — bloqués l'un par l'autre, à
reprendre ensemble plus tard). Tout le reste de la Vague 3 est fait.

| ID (source) | Item | Effort | État |
|---|---|---|---|
| **DEAD-01** (audit) | Supprimer 8 générations Opus/stat jamais promues (`opus_omnibus_v7[.py/_pretrained]`, `v9`, `v10_retrained`, `v11_no_ml`, `v11_followsetup[_no_ml]`, `opus_stat_retrained_v4`) — **8005 lignes** mortes au total avec DEAD-02. ⚠ NE PAS toucher `v8`, `v10`, `opus_stat_pretrained_v4` (dépendances réelles) | M | 🟡 **Analyse livrée (2026-07-19/20), re-confirmée le 2026-07-20 avec méthodologie de production**, décision utilisateur en attente — comparatif fonctionnel + empirique des 15 variantes sur 5 TF (15m/30m/1h/4h/1j), **re-run intégral le 2026-07-20** avec dimensionnement de fenêtre calqué sur la production (`auto_fetch_limit`/`split_is_oos`, plus de cap arbitraire) et **sans aucune stratégie sautée** (les 4 précédemment en timeout — v9/v10/v11_followsetup/v12 — ont maintenant un résultat optimisé complet), rapport HTML mis à jour remis à l'utilisateur (hors dépôt). Verdict par candidat, confirmé sur échantillons 2-4× plus grands : suppression nette pour 6/8 (`v7`, `v7_pretrained`, `v9`, `v10_retrained`, `v11_no_ml`, `opus_stat_retrained_v4`), 2 « discutables » (`v11_followsetup[_no_ml]`) — le dossier a changé de forme lors du re-run : la variante *no_ml* se renforce (1h optimisé passe positif), la variante ML s'affaiblit (score OOS de recherche positif partout mais aucun gain traduit sur le backtest complet, signal probable de surapprentissage à 10 essais). **Découverte hors périmètre DEAD-01** : `v11`/`v12` (actives en production) ressortent négatives sur leur TF de production (1h) dans ce test, positives seulement sur 4h — signalé pour examen séparé, non tranché. **Aucun fichier supprimé.** |
| **DEAD-02** (audit) | Supprimer `scoring_statistique_opus_v3.py` (579 L, aucun appelant) | S | ✅ FAIT — fichier + yaml supprimés, 0 référence |
| **DEAD-05** (audit) | Corriger le bug pyflakes `del ds_tr, ds_va` dans `opus_omnibus_v11.py:1065,1093` — **stratégie ACTIVE** (`manual_active: opus_omnibus_v11::30m`) | S | ✅ FAIT — try/finally englobant remplace les deux `del` dupliqués |
| **DEAD-06** (audit) | Supprimer 5 fonctions publiques jamais appelées : `config.strategy_file_path`, `execution.cap_notional`, `database.get_lifecycle_events`, `feature_store.get_provider`/`list_providers` | S | ✅ FAIT — les 5 supprimées |
| **DEAD-07** (audit) | 67 imports inutilisés (pyflakes, hors façade `indicators.py`) | M | ✅ FAIT — `ruff --select F` : 73 imports + 15 f-strings auto-fixés, 17 variables locales mortes retirées à la main |
| **DEAD-09** (audit) | Nettoyer `scripts/__pycache__` orphelin | S | ✅ Déjà propre — vérifié, rien à faire |
| **TEST-01** (audit + Plan C, doublon exact) | CI GitHub Actions (`pytest -m "not slow"`, lint) | S/M | ✅ FAIT — `.github/workflows/ci.yml` (lint ruff + pytest). **Nuance (2026-07-20)** : le job `lint` existait depuis Sprint 7 mais n'était **jamais passé** (773 erreurs `ruff check .` pré-existantes sur 163 fichiers, aucune liée à un sprint de ce document, jamais résorbées) — désormais **vert pour la première fois**, cf. §4.1bis |
| **TEST-04/05** (audit) = **TEST-02** (Plan C) | Config ruff (remplace flake8/mypy dispersés) : `ruff.toml`, `mypy.ini` | S/M | ✅ FAIT — `ruff.toml` + `mypy.ini`, `flake8` remplacé par `ruff` dans `requirements.txt` |
| **TEST-06** (audit) = **TEST-03** (Plan C) | Markers `pytest.ini` (`slow`, `strategy_smoke`), isoler les tests dépendant de `data/ohlcv` versionné | S/M | ✅ FAIT — `pytest.ini` avec les 2 markers ; aucun test ne dépend en fait de données versionnées (vérifié, tout est synthétique/`tmp_path`) |
| **TEST-11** (audit) = **TEST-04** (Plan C) | Tests smoke paramétrés pour les stratégies survivantes (~13/53 testées) | L | ❌ **Exclu explicitement de Sprint 7** — toujours bloqué par DEAD-01 |
| **DEAD-03** (audit) | Sort de `XRP_USDC` (données présentes, absent de `scanner.symbols`) | S | ✅ FAIT — **décision : ajouté à `scanner.symbols`** (`config.yaml`) ; pas de données `data/derivatives` pour XRP (gap connu, non bloquant) |

689/689 tests verts (649 avant Sprint 7 + 40 nouveaux Sprints 7/8).

### 4.1bis 🟢 PERF-02 (Plan C) — parallélisme réel de l'optimiseur — ✅ FAIT (2026-07-20)

Travail réalisé hors sprint numéroté, en marge de la préparation de la
décision DEAD-01 (nécessitait de faire tourner l'optimiseur sur les 15
variantes candidates × 5 timeframes en un temps raisonnable). Détail
technique complet dans `CHANGELOG.md` § « Post-Sprint 8 ». Résumé :

- `_SUPPORTED_TFS`/`_detect_timeframe` étendus à 4h/1j (14 fichiers,
  n'autorisaient que 15m/30m/1h en dur — exécution silencieuse sans signal
  au-delà).
- Cache d'entraînement process-wide branché sur `ml_dynamic_threshold`
  (sous-modèle de `opus_omnibus_v12`) — évitait jusqu'à ~40-60 % de coût par
  trial en retrains redondants. `random_search(n_trials=10)` sur 1h/8000
  barres : de « n'aboutit jamais en 300 s » à 67 s.
- **`random_search` : `n_jobs` réellement câblé** sur l'infra
  `ProcessPoolExecutor` déjà utilisée par `bayesian_search` (contexte
  `spawn`, cap mémoire anti-OOM, repli séquentiel) — la boucle restait
  séquentielle malgré le paramètre accepté. Mesuré ×3.0 sur 3 workers.
- `rolling_slope`/`rolling_hurst` (`app/core/indicators_market.py`)
  vectorisés (boucle Python O(n·window) → noyau/forme fermée), bit-exacts
  contre l'original. Mesuré ×233 / ×57.
- **Refonte de `param_search_optim`** (option de gel des paramètres à
  faible impact sur `random_search`/`bayesian_search`/`grid_search`, PAS un
  4e mode) : dépistage désormais **en budget** (les premiers essais de la
  recherche elle-même, jamais un essai en plus) et **pool de process
  partagé** entre dépistage et recherche (`_open_pool`/`_submit_wave`,
  remplace 3 blocs de création de pool quasi identiques). Mesuré sur
  `opus_omnibus_v12` : 137-140 s → ~63 s. Garde-fou `_MIN_SCREEN_PER_PARAM`
  ajouté après avoir mesuré que trop peu d'essais de dépistage vs nombre de
  paramètres gelait des paramètres sur un signal non fiable (mode
  facultatif uniquement — le mode grid, réduction obligatoire, n'est pas
  concerné).
- **Revue de code approfondie post-refonte** (pas seulement les tests
  unitaires) : 3 défauts trouvés et corrigés avant tout autre travail —
  `_run_parallel` comptait les succès au lieu des tentatives (ré-
  échantillonnage au-delà du budget, risque de `StopIteration` en mode
  grid) ; comptabilité fragile à la réutilisation d'instance de
  l'optimiseur ; `optimize_two_phase` ne propageait pas `param_search_optim`
  à `_dispatch` (toggle utilisateur silencieusement ignoré pour les jobs
  `ml_tune_hp`).
- 743/743 tests verts (+54 nouveaux/réécrits au total sur cet ensemble de
  travaux).

### 4.2 🟢 Quick wins financiers & sécurité — ✅ FAIT (Sprint 8), sauf FIN-01

Sprint 8 exécuté le 2026-07-18, **hors FIN-01** (exclu explicitement).

| ID (Plan C) | Item | Effort | Impact | État |
|---|---|---|---|---|
| **FIN-01** | Frais dynamiques par palier VIP OKX (`exchange.fee_schedule`, opt-in) | S | 5 | ❌ **Exclu explicitement de Sprint 8** — reste à faire |
| **FIN-04** | Benchmark vs Buy & Hold BTC (`app/core/performance.py`) | S | 4 | ✅ FAIT — déjà largement implémenté (`Backtester._add_buy_and_hold`) ; correctif du warmup figé à 210 (désynchronisé du warmup dynamique réel) |
| **FIN-06** | Compteur de frais par catégorie (taker/maker/borrow/stop) | S | 4 | ✅ FAIT — `Trade.fee_taker`/`fee_maker`/`exit_reason` (migration auto), `get_fee_breakdown()`, `GET /api/stats/fees` ; `exit_reason` distingue enfin clôture ≠ ouverture sur les 9 chemins de fermeture live |
| **FIN-07** | Slippage paper proportionnel à la taille | S | 3 | ✅ FAIT — `trading.paper_slippage_model: size` (défaut `static`), formule d'impact partagée avec le backtest (`app.core.execution.size_impact_cost`), volume lu depuis `OHLCVCache` |
| **STRAT-06** (Plan C) = **BT-13** (audit, jamais fait) | Compteur diagnostique `tp_sl_ambiguous_bars` (mesure, ne change pas la décision) | S | 3 | ✅ FAIT — `diagnostics.tp_sl_ambiguous_bars`, résolution stop-prioritaire inchangée |
| **SEC-04** | Rate-limiting granulaire par endpoint (au lieu du seul `default_limits` global `slowapi`) | S | 3 | ✅ FAIT — `Limiter` déplacé dans `app/api/state.py`, ~25 endpoints décorés `@state.limiter.limit(...)` |
| **SEC-05** | Backup automatique `trades.db` + `config.yaml` + `strategies/*.yaml` | S | 4 | ✅ FAIT — `deploy/backup.sh` (sqlite3.backup() Python, cohérent WAL), rétention automatique |
| **ARCH-07** (Plan C, partiel — §1.3.1) | Finir la migration des 16 littéraux `"BTC/USDC"` résiduels vers `DEFAULT_CONFIG_SYMBOL` | S | 2 | ✅ FAIT — les 3 sites de CODE Python migrés (`ohlcv_cache.py`, `config.py`) ; le reste (~28 occurrences) sont des docstrings/commentaires/valeurs par défaut UI HTML, hors scope Python |
| **BT-05** (audit, jamais fait) = **STRAT-03** (Plan C) | `scripts/audit_param_space.py` : lister chaque stratégie avec taille du param_space vs `n_trials`, warning si couverture < 1e-4 | M | 4 | ✅ FAIT — script + tests, `--strict` pour CI |
| **PERF-01** (Plan C) | Cache précompute indicateurs : 16 → 128 entrées, configurable | S | 3 | ✅ FAIT — `config.yaml:perf.precompute_cache_size` (défaut 128) |

### 4.3 🟡 Nettoyage résiduel architecture (effort réduit vs Plan C original)

| ID | Item | Effort réel | Note |
|---|---|---|---|
| **ARCH-05** (Plan C, partiel — §1.3.1) | Réduire `smart_money.py` (836 L) et `smart_money_signals.py` (702 L) sous 450 L chacun | M (pas L — la séparation en modules existe déjà, il s'agit d'extraire encore, pas de créer l'architecture) | Aucune urgence fonctionnelle, dette de lisibilité pure |
| **ARCH-03** (Plan C) = **ARCH-12** (audit, jamais fait, jugé optionnel) | `app/core/state.py::AppState` dataclass pour remplacer `app/api/state.py` | L | L'audit avait déjà conclu « l'encapsulation AppState complète reste optionnelle (aucune inversion restante) » après ARCH-04 audit — à ne faire que si un besoin concret apparaît (ex. plusieurs instances de bot par process) |
| **ARCH-01** (Plan C) | Extraire `OpusBase` (features V4, régime, train/predict partagés) pour les variantes Opus **survivantes** (v8, v10, v11, v12, `opus_stat_pretrained_v4`) | L | À faire **après** DEAD-01 (réduit le nombre de fichiers à traiter de 45 à ~37) |
| **STRAT-01** (Plan C) | Champ `status: experimental\|validated\|production\|archived` dans chaque YAML stratégie | M | Concept **distinct** du `SlotLifecycleManager` runtime existant (candidat/essai/actif/retiré, calculé) — ceci est une déclaration statique de maturité, filtrable dans l'UI `/config` |
| **STRAT-02** (Plan C) | Versioning modèles ML (hash features + date, `models/index.json`) | M | Aucun équivalent existant |
| **SEC-06** (Plan C) | Migrations SQLite via Alembic | M | **Nuance** : `_migrate_schema` idempotent (`ALTER TABLE` auto) existe déjà depuis OPS-08 (audit, Vague 4) — Alembic serait un upgrade d'outillage, pas un correctif de bug. Impact réel réduit vs la description du Plan C |
| **TEST-05** (Plan C) | Tests d'ordres live mockés (`MockExchange` complet : idempotence clientOrderId, partial fills, réconciliation frais, restauration crash) | L | Aucun équivalent — les tests actuels (`test_live_trader.py`, `test_position_lifecycle.py`) couvrent des scénarios plus étroits |
| **TEST-06** (Plan C) | Fixtures de non-régression backtest byte-identique (3 configs de référence, JSON snapshot) | M | Partiellement couvert par `test_execution_parity.py`/`test_generic_parity.py` mais pas de snapshot global multi-métriques |

### 4.4 🔵 Bets structurants — observabilité, sécurité avancée, DX

Aucun équivalent dans l'audit initial ni dans ce plan directeur — intégralement
issus du Plan C, tous confirmés **non démarrés** (fichiers/dépendances absents,
vérifié) :

| ID | Item | Effort | Impact |
|---|---|---|---|
| **OBS-01** | Métriques Prometheus (`prometheus-client` absent de `requirements.txt`, `app/core/metrics.py` inexistant) | M | 5 |
| **OBS-02** | Logs JSON + correlation IDs (`contextvars`) | M | 4 |
| **OBS-03** | Dashboard Grafana (dépend OBS-01) | M | 4 |
| **OBS-04** | Alertes Prometheus AlertManager (dépend OBS-01) | M | 4 |
| **OBS-05** | Tracing OpenTelemetry | L | 2 (backlog) |
| **DX-01** | Docker + docker-compose (`Dockerfile` absent) | M | 5 |
| **SEC-01** | Rotation secrets via vault (`SecretProvider` Protocol) | M | 4 |
| **SEC-03** | Validation Pydantic des payloads API (`app/api/schemas.py` inexistant) | M | 4 |
| **UI-02** | Refonte navigation 3 sections + `/onboarding` (`onboarding.html` inexistant — seul le regroupement sidebar a été fait par le rewrite frontend, confirmé par la note du Plan C lui-même §0.4) | M | 4 |

### 4.5 🟡 Itératif — reste du backlog Plan C (non re-vérifié en détail)

Ces items n'ont pas été vérifiés ligne à ligne contre le code dans cette
session (faible risque d'être déjà faits vu qu'ils créent tous des fichiers
absents du repo — `app/core/regime.py`, `sentiment.py`, `tax_report.py`,
`app/ml/trainer.py` versioning, etc. — confirmé par `ls` négatif sur les
cibles principales). Conservés tels quels, priorité inchangée par rapport au
Plan C :

- **FIN-02** (borrow rate dynamique), **FIN-03** (reporting fiscal FIFO, dépend SEC-06), **FIN-05** (Sharpe/Sortino/Calmar/VaR/CVaR temps réel), **FIN-08** (réconciliation PnL quotidienne), **FIN-09** (multi-devises, backlog).
- **DX-02** (setup interactif), **DX-03** (hot-reload dev), **DX-04** (ADR), **DX-05** (profiling intégré), **DX-06** (OpenAPI enrichi).
- ~~**PERF-02** (parallélisation optimiseur)~~ — ✅ **FAIT** (2026-07-20, cf. §4.1bis), retiré de ce backlog. **PERF-03** (PostgreSQL, backlog), **PERF-04** (streaming SSE backtests).
- **LIFE-01** (tests transitions cycle de vie), **LIFE-02** (timeline UI), **LIFE-03** (auto-re-opt, backlog), **LIFE-04** (allocation graduelle, backlog).
- **WKFLOW-01/02/03** (conventional commits, pre-commit, templates issues/PR).
- **RES-01** (regime detection HMM), **RES-02** (backtest portfolio multi-actifs), **RES-03** (sentiment F&G, backlog), **RES-04** (extension usage dérivés).

---

## 5. Roadmap par sprints (mise à jour)

### Sprints terminés

| Sprint | Contenu | Statut |
|---|---|---|
| 0 | 5 correctifs P0 sécurité financière/config | ✅ `ea9706e` |
| 1 | 8 items concurrence/sécurité P1 | ✅ `7aaed6b` |
| 2 | G1 — abstractions multi-actifs | ✅ `49be475` |
| 4 | Qualité métriques & optimiseur | ✅ `78ae183` |
| — | Vérification ARCH-04/05/06 (numérotation Plan C) | ✅ toutes obsolètes/déjà faites (§1.3.1) |
| 7 | Nettoyage code mort + CI/lint (Vague 3 de l'audit), hors DEAD-01/TEST-11 | ✅ `d19c978` |
| 8 | Quick wins financiers & sécurité, hors FIN-01 | ✅ (ce commit) |
| Post-8 | Comparatif DEAD-01 (4h/1j) + PERF-02 (parallélisme réel optimiseur, refonte `param_search_optim`) + résolution dette lint pré-existante (773 → 0 erreur, `ruff check .` vert pour la 1ère fois) | ✅ `5022b69`…`3bbcd55` (§4.1bis) |

### Sprints à venir (reformulés après fusion)

| Sprint | Contenu | Dépendances | Détail |
|---|---|---|---|
| **7 (reste)** | DEAD-01 (8 générations Opus/stat mortes) + TEST-11 (smoke stratégies) | analyse livrée (2026-07-19/20) — **décision utilisateur de suppression en attente** ; TEST-11 reste bloqué par DEAD-01 | §4.1 |
| **8 (reste)** | FIN-01 (frais dynamiques VIP OKX) | aucune, exclu explicitement du Sprint 8 initial | §4.2 |
| **3** | G2 — SBF120 en paper (données, calendrier, frais, sizing) | Sprint 2 (fait) | §3, inchangé |
| **9** | Observabilité & DX (Prometheus, Docker, Pydantic, onboarding) | aucune | §4.4 |
| **10** | Refactor stratégies avancé (OpusBase, status lifecycle, versioning ML, tests ordres mockés) | Sprint 7 (DEAD-01 réduit le périmètre d'ARCH-01) | §4.3 |
| **5** | G3 — exécution réelle actions (IBKR/Saxo) | Sprint 3 validé en paper | §3, inchangé |
| **11** | Itératif (financier, DX, perf, cycle de vie, workflow, recherche) | au fil de l'eau | §4.5 |

**Décisions produit en attente** (à trancher avec l'utilisateur avant
exécution, non bloquantes pour le reste) :
- Confirmation que BT-11/STRAT-05 (plafond corrélation BTC/ETH) reste décliné.
- Sort de `XRP_USDC` : **tranché** (Sprint 7) — ajouté à `scanner.symbols`.
