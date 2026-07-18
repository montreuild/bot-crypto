# 📝 Changelog

Historique des versions du Crypto Bot.

---

## [Non publié]

### 🛡 Sprint 0 — Correctifs P0 sécurité financière & config

- **[S0-01]** Sync spot/margin propage l'équité à l'allocateur
  (`allocator.update_equity`) — les budgets de slots suivaient l'ancien capital.
- **[S0-02]** Retour de `create_order` validé sur les 5 sites d'appel
  (ouverture, stops/OCO, scale-in, clôture) : `None`/status
  `rejected|canceled|expired` → plus de position fantôme ; un échec de
  CLÔTURE remet la position en gestion + alerte critique.
- **[S0-03]** CORS : `DELETE`/`PUT` ajoutés (`/api/optimize/job` était
  inutilisable depuis le frontend).
- **[S0-04]** `_expand_env` strict : variable `${...}` absente = démarrage
  refusé en live (WARNING en paper) — fini les credentials vides silencieux.
- **[S0-05]** `scripts/setup.sh` génère un `.env` avec `WEB_API_KEY`
  aléatoire ; `.env` ajouté au `.gitignore` ; `web.api_key: ${WEB_API_KEY}`.

### 🔒 Sprint 1 — Concurrence & sécurité

- **[S1-01]** `RLock` sur `RiskManager` (23 méthodes `@_locked`) — état
  partagé thread trading / threads API.
- **[S1-02]** `RLock` sur `OHLCVCache` (fetch réseau hors verrou).
- **[S1-03]** `update_equity`/`check_correlation` verrouillées
  (`CapitalAllocator`).
- **[S1-04]** Auth WebSocket par cookie HttpOnly (query param en fallback
  non-navigateur).
- **[S1-05]** `NEXT_PUBLIC_API_KEY` supprimée du bundle client —
  `credentials: 'include'` + `EventSource withCredentials`.
- **[S1-06]** Routes backtest/replay/optimizer : vérifié non bloquantes
  (threadpool AnyIO de Starlette) — aucun changement requis, documenté.
- **[S1-07]** `_pre_execution_check` renforcé : solde spot libre réel et
  margin level critique vérifiés avant chaque entrée.
- **[S1-08]** Section `live.trailing` dédiée — le trailing live ne suit plus
  silencieusement les changements de `backtest.*` (repli + WARNING).

### 🌍 Sprint 2 — Abstractions multi-actifs (G1, comportement crypto inchangé)

- **[S2-01]** `app/core/providers.py` : protocoles `MarketDataProvider`/
  `ExecutionProvider` (PEP 544) — `RobustExchange` conforme sans modification.
- **[S2-02]** `Venue` étendue : `asset_class`, `quote_currency`, `tick_size`,
  `lot_size`, `fractional`, `allow_short` ; `venues.assign[symbol]` supporté.
- **[S2-03]** Devise de cotation neutralisée : `min_volume_quote_24h` (alias
  rétro-compatible de `min_volume_usdc_24h`), scan dynamique par
  `Venue.quote_currency` au lieu du littéral `/USDC`.
- **[S2-04]** `BaseStrategy.asset_classes` + marquage crypto-only de
  `funding_flow`/`derivatives_reversion` + filtre dans `_build_active_per_tf`.
- **[S2-05]** Golden test de parité backtest BTC/USDC avant/après
  (`tests/test_generic_parity.py`).

### 📊 Sprint 4 — Qualité des métriques & optimiseur

- **[S4-01/02]** Sharpe live aligné sur le backtest : courbe d'équité
  synthétique par trade, ordre chronologique corrigé (`get_trades` renvoie
  DESC), annualisation par `bars_per_year(tf)` (source unique
  `app/core/timeframes.py`) au lieu d'un `sqrt(252)` fixe.
- **[S4-03]** Data leakage du ré-entraînement ML final documenté par design +
  mode opt-in `optimizer.ml_final_train_mode: is_only`.
- **[S4-04]** Deflated Sharpe Ratio (Bailey & López de Prado 2014) dans
  `opt_scoring.py` — stdlib `statistics.NormalDist`, autonome (pas encore
  câblé dans `composite_score`, suivi séparé).
- **[S4-05]** Métriques par stratégie en une passe (pré-groupement, fini le
  triple refiltrage O(n×k)).
- **[S4-06]** `_load_db_stats` : agrégats globaux en SQL
  (`get_trade_global_aggregates`, COUNT/SUM/MAX).
- **[S4-07]** Test de régression scale-in/budget cumulé (l'item « scale-in
  sans validation » du plan d'amélioration était faux — `can_allocate`
  couvre déjà le cumul).

### 🧹 Sprint 7 — Nettoyage code mort + CI/lint (Vague 3 de l'audit)

Périmètre §4.1 du plan directeur, hors **DEAD-01** (8 générations Opus/stat
jamais promues) et **TEST-11** (tests smoke stratégies, bloqué par DEAD-01) —
exclus explicitement de ce sprint.

- **[DEAD-02]** Suppression de `scoring_statistique_opus_v3.py` (579 lignes)
  et de `strategies/scoring_statistique_opus_v3.yaml` — zéro appelant
  (grep exhaustif Python + YAML).
- **[DEAD-03]** `XRP/USDC` ajouté à `scanner.symbols` (`config.yaml`) —
  données OHLCV présentes sur disque mais symbole absent du scan ;
  pas de données `derivatives` pour XRP (gap connu, sans impact bloquant).
- **[DEAD-05]** `opus_omnibus_v11.py` (stratégie **active**,
  `manual_active: opus_omnibus_v11::30m`) : les deux `del ds_tr, ds_va`
  dupliqués (bloc 1050-1094) remplacés par un `try/finally` englobant —
  suppression garantie une seule fois par itération, plus de dépendance à
  deux points de sortie distincts.
- **[DEAD-06]** Suppression des 5 fonctions publiques jamais appelées :
  `config.strategy_file_path`, `execution.cap_notional`,
  `database.get_lifecycle_events`, `feature_store.get_provider`/
  `list_providers` — zéro référence externe re-vérifiée avant suppression.
- **[DEAD-07]** Nettoyage `ruff --select F` (façade `indicators.py`
  exclue via `ruff.toml`) : 73 imports inutilisés + 15 f-strings sans
  placeholder auto-fixés ; 17 variables locales mortes retirées à la main
  (lecture des lignes concernées confirmée sans effet de bord avant
  suppression).
- **[DEAD-09]** `scripts/__pycache__` déjà propre (aucun `.pyc` résiduel) —
  vérifié, rien à faire.
- **[TEST-01]** `.github/workflows/ci.yml` : job `lint` (`ruff check .`) +
  job `test` (`pytest tests/ -q -m "not slow"`), Python 3.12.
- **[TEST-04/05]** `ruff.toml` (remplace flake8/pyflakes — `line-length=120`
  aligné sur la convention `CONTRIBUTING.md`, règles `F/W/E/I`) et
  `mypy.ini` (non strict, `ignore_missing_imports`) ; `flake8` retiré de
  `requirements.txt` au profit de `ruff==0.15.8`.
- **[TEST-06]** `pytest.ini` : markers `slow`/`strategy_smoke` déclarés
  (aucun test actuel ne dépend de `data/ohlcv`/`data/derivatives` versionnés
  — vérifié, toute la suite tourne sur données synthétiques/`tmp_path` ;
  rien à isoler pour l'instant).
- 649/649 tests verts après chaque étape.

### 💰 Sprint 8 — Quick wins financiers & sécurité

Périmètre §4.2 du plan directeur, hors **FIN-01** (frais dynamiques par
palier VIP OKX) — exclu explicitement de ce sprint.

### 📚 Documentation

- `docs/PLAN_DIRECTEUR_MULTI_ACTIFS.md` : fusion des 3 plans (audit
  `docs/audit/`, plan complémentaire du 14/07, plan multi-actifs) — état
  vérifié contre le code, backlog consolidé (§4), ~119 items faits /
  ~81 restants.
- `docs/audit/00-INDEX.md` : Vagues 0-2/4-6 marquées réalisées, Vague 3
  identifiée non réalisée (reprise en Sprint 7 du plan directeur).

---

## [12.17.0] - 2026-07-11

### 🛡 Audit Vagues 1-2 : sécurité + intégrité de la mesure (14 items)

**Vague 1 — Sécurité (docs/audit)** :
- **[UI-01]** XSS corrigée : échappement unifié sur `escHtml()` partagé
  (data.html laissait passer les guillemets dans un attribut onclick →
  boutons passés en data-attributes + délégation d'événement).
- **[OPS-02]** `web.host=0.0.0.0` sans `web.api_key` → **blocage** au
  chargement (override dev : `ALLOW_INSECURE_WEB=1`).
- **[OPS-03]** Unit systemd `crypto-bot-watchdog.service` : le dead-man
  switch a enfin un lecteur en production.
- **[OPS-04]** `paper_mode=false` sans canal de notification → log CRITICAL.
- **[OPS-05]** 6 endpoints GET (settings/changelog/optimize status/stream/
  results/spaces) exigent désormais la clé API.
- **[OPS-07]** CandleStore : écriture parquet **atomique** (tmp + os.replace).

**Vague 2 — Intégrité de la mesure** :
- **[BT-02]** Monte-Carlo réparé : bootstrap avec remise pour l'équité
  finale/prob_profit (la permutation donnait p5=p95) ; permutation conservée
  pour drawdown/ruine (statistiques d'ordre).
- **[BT-03]** `max_notional_pct` unique (0.20) backtest/live — le backtest
  plafonnait à 50 % du capital quand le live exécute 20 %. Delta documenté
  (smart_money BTC 4h : FULL +483→+329 mais PF 1.67→1.90 — le backtest dit
  désormais la vérité du live).
- **[ARCH-01]** Parité : `_merge_params` (live) filtre `_GLOBAL_PARAM_KEYS`
  comme le backtest — une clé globale glissée dans optimizer_results ne peut
  plus modifier le risque en live.
- **[BT-04]** Le bouton « Appliquer » passe le MÊME garde-fou que l'auto-apply
  (refus 409 motivé, override `force=true` loggé).
- **[BT-06]** Seuils unifiés (app/core/stats_thresholds.py) :
  MIN_SIGNIFICANT_TRADES=10 pour toute décision engageante (apply, promotion
  ACTIF — fidelity_min_fills 2→10) ; 2 réservé à l'anti-dégénérescence.
- **[BT-07]** Gate **walk-forward** sur l'auto-apply : best_params figés,
  consistency ≥ 60 % sur 5 folds requise (désactivable optimizer.wf_gate).
- **[BT-08]** Convention IS/OOS unique (app/core/is_oos.py) : WARMUP=210 et
  fraction 0.35 dédupliqués (3 sites auto_optimizer + optimizer) —
  byte-identique (test d'équivalence des indices de coupure).
- **[BT-09]** Le backtest applique la même **courbe de dé-risquage en
  drawdown** que le live (×0.75 >5 %, ×0.5 >10 % — app/core/risk_curve.py).
  BTC 4h byte-identique (aucun DD >5 % sur ce run).

25 nouveaux tests. 508 tests OK.

### 🛠 Audit Vague 0 : régressions per-symbole corrigées (BT-01, BT-12, OPS-01, UI-02/03/04)

Exécution de la « Vague 0 » du plan d'audit (docs/audit/00-INDEX.md) — les six
chemins secondaires qui supposaient encore l'ancien slot 2-parties :

- **[BT-01]** `/api/optimize/apply` transmet désormais le `symbol` du job à
  `apply_best_params` — un apply manuel n'écrase plus le mapping par symbole
  des autres paires. Tests de coexistence + migration d'entrée héritée.
- **[BT-12]** `/api/optimize/start` accepte `symbols` (CSV) : boucle
  fetch+jobs par symbole (réponse `per_symbol`), mono-symbole inchangé.
- **[OPS-01]** Clés héritées 2-parties (`slot_budgets`, `disabled_slots`,
  `manual_active`) : repli par préfixe vers les slots 3-parties (helper
  `_lookup_legacy`, log au chargement, priorité à la clé exacte ; la
  désactivation d'un forçage lève aussi la clé héritée). 6 tests.
- **[UI-02]** Écran /config : panneau « Overrides par symbole » par stratégie
  (sélecteurs TF+symbole, éditeur, liste des overrides existants) ; l'API
  `strategy-params` accepte `timeframe`+`symbol` et écrit dans
  `optimizer_results[tf][symbole]` via `apply_best_params` (oos_score
  préservé, base intacte) ; nouveau `GET /api/config/strategy-overrides`.
- **[UI-03]** /audit n'écrase plus les résultats OOS entre symboles ;
  `backtest_history` passe au slot 3-parties.
- **[UI-04]** /trades : filtre « Slot » à 3 parties (`strat::tf::symbole`,
  libellé « Strat TF · Paire ») — ne mélange plus les paires.

483 tests OK (13 nouveaux).

---

## [12.16.0] - 2026-07-11

### 🎯 Configs par symbole : `optimizer_results[strat][tf][symbol]` + slots par symbole

**Séparation complète des configs par symbole** : une stratégie a une config BTC
ET une config ETH distinctes qui **coexistent** (l'une n'écrase plus l'autre), au
lieu d'un unique jeu par `(stratégie, timeframe)` partagé par tous les symboles.

**Phase 1 — résolution des paramètres**
- **`resolve_strategy_params(cfg, tf, symbol)`** accepte un `symbol`. Schéma
  `optimizer_results[strat][tf][symbol]`, **rétro-compatible** (`_select_symbol_entry`) :
  une entrée HÉRITÉE (sans dimension symbole) est réputée calibrée pour **BTC/USDC**
  (`DEFAULT_CONFIG_SYMBOL`) et **ne s'applique plus aux autres symboles** (avant,
  la config BTC déteignait silencieusement sur ETH).
- `Backtester.run` + routes scanner transmettent le `symbol`. **BTC 4h
  byte-identique** ; ETH retombe sur ses params de base sauf config dédiée.

**Phase 2 — notion de slot `strategy::tf` → `strategy::tf::symbol`**
- **`get_active_strategies_per_tf`** sélectionne les slots actifs **par (tf, symbole)**
  (top-N par symbole ; itère `scanner.symbols`).
- **`bot_identity`** (`slot_key`, `bot_id`, générations, `resolve_venue` avec
  précédence `strat::tf::symbol`), **`capital_allocator`** (`SlotBudget.symbol`,
  clés de slot), **`signal_pipeline`** (score par symbole), **`live_trader`**,
  **`oos_tracker`** (+ `get_closed_trades_for_slot(symbol=…)`), **`apply_best_params`**
  (écrit sous `[tf][symbol]`, migre une entrée héritée vers BTC/USDC) : tous
  câblés sur le symbole. 12 tests (résolution + slots + identité + allocateur).

**Activation + auto-optimiseur par symbole**
- **`trend_rider` activée** (`enabled: true`) : sur 4h elle tourne sur **ETH/USDC**
  (config OOS +286) ; sur BTC elle reste sous le top-N, donc n'y trade pas.
- **Auto-optimiseur bouclé par symbole** : la ré-optimisation planifiée et la
  re-optimisation de cycle de vie itèrent désormais `scanner.symbols` — chaque
  paire écrit sa propre `optimizer_results[tf][symbol]` (via `apply_best_params`
  avec `symbol`). L'auto-apply reste borné par « bat la baseline » et la
  viabilité OOS, donc une paire où une stratégie ne marche pas ne s'active pas.

**Phase 3 — Web / UI**
- `bot_identity.parse_slot_key` (décompose `strat::tf[::symbol]`, robuste aux "/"
  des symboles). Route **portfolio** : parse 3-composantes + expose `symbol` par
  bot. Route **trades** `/api/strategy/{slot_key}/performance` : accepte le format
  à symbole et filtre les trades par symbole. Écran **Bots** : puce symbole sur
  chaque carte + aide mise à jour (bot = stratégie × timeframe × symbole).

**Calibration (vrai Backtester, 4h, OOS 2024+)**
- **smart_money** : edge **spécifique BTC** (OOS +148). **Négatif OOS sur ETH pour
  les 24 configs testées** → non calibrable sur ETH ; reste BTC-only (config héritée).
- **trend_rider** : config **ETH dédiée** ajoutée (`adx_min=22, chop_max=60,
  trail_mult=3.5`) → **OOS +286 / PF 1.27 / 77 tr** (vs BTC OOS ~+44). Les deux
  coexistent dans `trend_rider.yaml`. Résultat concret : sur 4h, `trend_rider`
  tourne sur **ETH/USDC** et `smart_money` sur **BTC/USDC**, chacun sa config.

---

## [12.15.0] - 2026-07-10

### 🔀 SMT divergence : primitive moteur + câblage smart_money (MESURÉ non pertinent)

Nouvelle primitive **`smc.smt_series(df, correlate_path, lookback)`** (moteur,
générique) : divergence SMT `{−1, 0, +1}` entre l'actif tradé et un actif corrélé
(Parquet/CSV), aligné CAUSALEMENT par timestamp via `ict.align_series` +
`ict.smt_divergence`. Dégradation gracieuse (None si corrélé absent). `vizion`
délègue désormais son `_load_smt` à cette primitive (dé-duplication, cohérence).

**Câblage dans `smart_money`** (paramètres OFF par défaut → byte-identique,
vérifié par l'empreinte de régression) : `smt_bonus` (+`smt_conf` au score si la
divergence CONFIRME le sens), `smt_filter` (rejette un setup CONTREDIT par une
divergence opposée), point d'injection unique à la résolution des candidats.
Corrélé configurable (`smt_correlate_path`, ex. ETH pour BTC). Ajoutés au
`param_space` (leviers d'optimiseur).

**Mesure honnête (vrai Backtester, 4h, split 2/3 OOS=2024+)** :

| Variante | BTC 4h OOS | ETH 4h OOS |
|----------|-----------|-----------|
| baseline | +148 (PF 1.46, 52 tr) | −93 (PF 0.74, 51 tr) |
| SMT bonus | **identique** | **identique** |
| SMT filter | −0.1 / −1 trade | **identique** |

→ **SMT n'est PAS pertinent comme confluence/filtre à la barre d'ENTRÉE de
smart_money.** Mécanisme : le signal SMT ne se déclenche qu'à un nouvel EXTRÊME
sur `lookback` (≈ 9 % des barres), or smart_money entre sur des RETESTS d'OB/
breaker — loin des extrêmes → recouvrement quasi nul aux barres d'entrée (0 à 1
trade modifié sur 8 ans). Le câblage est CONSERVÉ (off par défaut, levier
d'optimiseur) ; une variante « SMT à la barre du SWEEP » resterait à tester.

### 🔌 Données OHLCV : correctif fetch + page « Données » + Fast Analyse Scanner

- **Fix cache** : `CandleStore._load` force désormais l'ORDRE canonique des
  colonnes (`time` en premier). Corrige le crash `unable to vstack, column names
  don't match: "open" and "time"` provoqué par un Parquet écrit avec un ordre
  différent — le `pl.concat` ne casse plus, et les fichiers déjà écrits sont
  réparés au prochain fetch.
- **Fix « 0 bougie »** : le `since` du premier fetch (full/historical) est
  désormais borné à `2017-01-01` (`_MIN_SINCE_MS`). Corrige le cas où
  `now - 50000 × 4h ≈ 2003` faisait renvoyer un tableau VIDE par l'exchange
  (OKX/Binance rejettent un `since` trop ancien) → plus aucune paire ne reste
  bloquée à 0 bougie.
- **Nouvelle page `/data` (« 🗄 Données »)** : tableau de l'état du cache par
  paire/TF (bougies, plage de dates, taille) via `GET /api/data/status`, plus
  un formulaire de **fetch manuel** sur une paire + un TF ARBITRAIRES (pas
  forcément dans la config) via `POST /api/data/refetch?symbol=&tf=&bars=`, et
  un « Recharger tout (config) ». Remplace l'ancien bouton dashboard.
- **Cadre « ⚡ Fast Analyse & optimisation »** (Scanner, entre Graphique et
  Prédictions) : screening d'indicateurs (tendance / retour-à-la-moyenne, frais
  taker vs maker, split IS/OOS) sur la paire + TF affichés, via
  `GET /api/scanner/fast_analysis`. Reprend la logique de l'ancien script
  `analyze_indicators.py`, désormais intégrée à l'UI.
- **Nettoyage** : suppression des scripts `fetch_data.py` / `analyze_indicators.py`
  et de tout le support actions/ETF/Yahoo (CAC 40, Eutelsat, Capital B). Le
  moteur est 100 % crypto (OKX/Binance via ccxt).

### 🎬 Modèle @Vizion-fr : détecteurs + stratégie `vizion` (stricte)

D'après les transcriptions de deux vidéos ICT de @Vizion-fr (entrée sur Order
Block validée par le timeframe alignment). Nouveaux détecteurs dans `ict.py` :
- **propulsion_block** : OB imbriqué dans un OB antérieur de même sens ;
- **nested_order_block** : imbrication multi-timeframe (OB LTF ⊂ OB HTF) ;
- **align_series** : alignement causal générique de deux actifs crypto par
  timestamp — préparation d'une SMT divergence sur N'IMPORTE quel couple corrélé.

Helper moteur **`smc.htf_analysis`** (bucketing causal partagé avec
`htf_trend_series` via `_htf_buckets` — extraction **byte-identique**, vérifiée)
qui expose l'analyse SMC HTF + le mapping LTF→bucket pour récupérer les OB HTF
actifs à chaque barre.

Nouvelle stratégie **`vizion`** (`enabled: false`) appliquant STRICTEMENT la
checklist : OB avec la tendance en discount, ayant pris de la liquidité,
déclenché sur rebalance de FVG, IMBRIQUÉ dans un OB HTF, confirmé par SMT
(chemin d'actif corrélé GÉNÉRIQUE, dégradation gracieuse si absent). Chaque case
est un paramètre activable → mesurable isolément.

**Mesure honnête (BTC 4h, sans SMT)** : la checklist stricte donne **1 seul
trade** sur 8 ans (0 OOS) — les gates cumulés (sweep+FVG+imbrication HTF)
filtrent presque tout. Relâchée à « OB en discount avec la tendance », c'est
+66 OOS (48 trades) — mais c'est déjà le cœur du smart_money. Confirme le caveat
des vidéos (exemples cherry-pické) : le modèle strict n'est pas tradable sur BTC
faute d'échantillon. À re-mesurer sur d'autres TF/actifs et avec SMT (données
ETH). 453 tests OK.

### 🧊 app/core/ict.py — détecteurs ICT automatisables (briques réutilisables)

Nouveau module regroupant les concepts ICT (Inner Circle Trader) automatisables
qui manquaient au moteur SMC, en primitives PURES et CAUSALES — non câblées à une
stratégie (on mesurera/activera ensuite, discipline du projet) :

- **Consequent Encroachment** (niveau 50 % d'un FVG) ;
- **Balanced Price Range** (chevauchement FVG haussier + baissier) ;
- **Unicorn model** (Breaker chevauché par un FVG de même polarité) ;
- **Projections en écarts-types** d'une jambe (grille de TP −1/−2/−2.5/−4 SD) ;
- **Silver Bullet windows** (fenêtres horaires ICT, UTC) ;
- **Judas Swing** (faux mouvement à l'ouverture de session, sweep + reclose) ;
- **SMT divergence** (deux actifs corrélés alignés divergent sur un extrême —
  le plus prometteur, exploitable dès qu'on aura ETH/SOL en local).

Toutes causales (signal à ``i`` ⇐ données ≤ ``i``, vérifié). Le reste du canon
ICT (structure, liquidité, sweeps, OB, FVG, voids, premium/discount, OTE,
killzones, HTF, AMD) est déjà dans le moteur smart_money.

**Cohérence** : trois détecteurs purs ICT qui vivaient dans la *stratégie*
smart_money — `fvg_overlap`, `inverted_fvg_overlap` (IFVG), `measured_move_target`
(symétrie de jambe) — sont **extraits vers `ict.py`** (primitives réutilisables),
la stratégie les importe. Backtest **byte-identique** (config 4h : 168 signaux,
empreinte inchangée). Les helpers du MOTEUR (`killzone_flags`, `premium_discount`,
`liquidity_targets`…), couplés au graphe d'entités et partagés avec la route
scanner, restent dans `smc.py` (API publique du moteur). 445 tests OK.

### ❌ Stratégie LTF mean-reversion : NON construite (mesurée perdante sur BTC)

Demande : bâtir une stratégie LTF mean-reversion « maker ». Après mesure
rigoureuse (BB/RSI/VWAP, filtres range, reclaim confirmé, 15m/30m/1h, frais
taker ET maker), **le mean-reversion est négatif sur BTC quel que soit le
régime de frais** : BTC est un actif de MOMENTUM, il traverse les bandes au lieu
de réverser. Le positif-sous-maker mesuré précédemment était l'entrée
STRUCTURELLE (SMC), pas du mean-reversion. Conformément à la discipline (ne pas
livrer d'edge négatif), aucune stratégie n'a été créée. Le cadre
« ⚡ Fast Analyse » du Scanner permet désormais de trouver un symbole où le
mean-reversion fonctionne réellement (instruments range-bound).

---

## [12.14.0] - 2026-07-09

### 🆕 Nouvelle stratégie `trend_rider` (indicateurs classiques, long-biaisée)

Stratégie construite UNIQUEMENT à partir d'indicateurs existants, par campagne
de mesure méthodique :

1. **Screening individuel** de ~22 indicateurs comme signal autonome (BTC 4h,
   sortie identique) : aucun n'a d'edge fort ; seul l'alignement de tendance
   EMA200 + EMA20/50 est positif sur toutes les périodes.
2. **Combinaison** : la confluence naïve et les triggers momentum sur-tradent et
   perdent ; le **long-only** (biais haussier crypto — les shorts saignent) sur
   un « trend-hold » filtré régime devient positif sur les deux périodes.

Setup : entrée LONG au **front montant** d'un régime de tendance filtré
[close > EMA200, EMA20 > EMA50, ADX > seuil, DI+ > DI−, choppiness < 55], sortie
**trailing** (laisse courir), SL initial 1,5×ATR. Confluences (CVD, volume, ADX
fort, bougie, RSI) disponibles pour le sizing.

Validation BTC 4h (vrai Backtester) : **FULL +370 (PF 1.18, Sharpe 2.4), OOS
2024-26 +49 (PF 1.11, oos_score +0.183)** — tradable, edge modeste et
**indépendant** du smart_money structurel. 1d testé : OOS négatif (non tradable).

Fichiers : `app/strategies/trend_rider.py` + `strategies/trend_rider.yaml`.
**`enabled: false`** — complément expérimental à valider en forward avant toute
promotion live. 7 tests unitaires (contrat, front de régime, long-only,
causalité live↔backtest). 435 tests OK.

---

## [12.13.0] - 2026-07-08

### 🔬 smart_money : re-test 15m/30m/1h avec l'arsenal complet — restent non tradables

Question : les LTF peuvent-ils devenir tradables avec les nouveaux leviers
(trailing, sizing, choppiness, confirmation bougie) ? Batterie complète relancée
par TF sur le vrai Backtester. **Réponse : non.** Le combo 4h ne transfère pas —
le trailing DÉGRADE les LTF (bruit). Seule la sélectivité extrême aide, sans
jamais rendre l'OOS positif :

| TF | baseline OOS sc | meilleur (choppiness<50) | tradable |
|---|---|---|---|
| 1h | −0.078 | **−0.021** (OOS −10,3 vs −38,8) | ❌ |
| 30m | −0.155 | **−0.098** (OOS −48,8 vs −77,4) | ❌ |
| 15m | −0.079 | **−0.045** (OOS −22,6 vs −39,3) | ❌ |

Structurel : SMC sur BTC en LTF est dominé par le bruit + les frais 0,1 %/côté
sur des jambes trop courtes ; filtrer agressivement stoppe l'hémorragie mais
effondre le nombre de trades (chiffres uniques) sans créer d'edge. Le filtre
`chop_filter_max: 50` est enregistré dans les configs 1h/30m/15m comme « moins
pire » (cohérent avec la philosophie du fichier), scores OOS mis à jour. Seul le
4h reste tradable.

### 🎯 smart_money : croisement indicateurs × SMC (choppiness + confirmation bougie)

Croisement des nouveaux indicateurs avec la stratégie SMC (chacun testé comme
filtre/confluence sur la config 4h via le vrai Backtester). **Deux gagnants nets
activés sur le 4h**, le reste neutre/négatif :

- **Filtre Choppiness < 61.8** (`chop_filter_max`) : ne trader QU'hors congestion
  (l'idée « OB en tendance, pas en chop »). FULL +387→**+483** (PF 1.51→1.69,
  Sharpe 5.2→6.7), OOS +108→**+117**, et **DD réduit** (−14 %→−9 %).
- **Confirmation bougie** (`candle_bonus`) : bonus +0.05 si pin bar / engulfing
  dans le sens du setup → via le sizing, monte les setups confirmés (qualité par
  trade très élevée). OOS +117→**+129**, score 0.359→**0.372**, trades constants.

Écartés (mesurés neutres/négatifs) : VWAP session (coupe les entrées de repli),
CVD slope (coupe trop), RSI-divergence, VSA.

Config 4h finale : trailing 3.5×ATR + time-stop conditionnel 12 + sizing par
confluence + choppiness<61.8 + confirmation bougie → **FULL +503 (PF 1.72,
Sharpe 6.8, DD −8.7 %), OOS +129 (PF 1.55, score 0.372)**. Tous OFF par défaut
(byte-identique), ajoutés au `param_space` ; seul le 4h les active. 428 tests OK.

### 📐 indicators_core : nouveaux indicateurs réutilisables (VWAP, CVD, Choppiness…)

Batch d'indicateurs génériques (importables par toute stratégie via la façade
`app.core.indicators`), en vue de les croiser avec SMC :

- **VWAP** : `rolling_vwap` (glissant), `session_vwap` (ancré jour UTC),
  `vwap_bands` (± k×σ, cibles/sur-extension).
- **CVD** (`cvd`) : Cumulative Volume Delta approximé OHLCV (multiplicateur
  money-flow) — divergences prix/CVD = absorption.
- **Choppiness** (`choppiness`) : tendance (< 38.2) vs congestion (> 61.8).
- **Keltner** (`keltner`) : EMA ± mult×ATR (canal, cibles TP).
- **Value Area** : `smc.volume_profile` renvoie désormais `va_low`/`va_high`
  (70 % du volume autour du POC) — additif, signaux SMC inchangés.
- **Price action** : `pin_bar` (marteau/étoile), `engulfing` (avalement),
  `vsa_signal` (No Demand / No Supply).
- **Divergences cachées** (`rsi_divergence_hidden`) : continuation, complément
  de `rsi_divergence` (régulières).

Toutes causales (fenêtres passées + barre courante), pures, testées (5 tests).
Microstructure (carnet d'ordres, spoofing, niveaux de liquidation) NON incluse :
nécessite des données L2/temps réel non backtestables sur OHLCV historique.

### 🧩 smart_money : 3 pistes SMC optionnelles (OFF par défaut)

Audit complet de la stratégie contre la checklist SMC de référence : **15/19
concepts déjà implémentés et validés** (swings, BOS/CHoCH, BSL/SSL, sweeps, FVG,
voids, OB avancés, breakers, premium/discount, confluence multi-facteurs, SL
dynamique, TP-liquidité, killzones, MTFA). Les 4 restants ont été mesurés
individuellement ET conjointement sur BTC 4h : tous perdants ou déjà couverts.

Trois sont implémentés **désactivés par défaut** (backtest byte-identique,
exposés au `param_space` pour d'autres TF/symboles/régimes) :

- `ext_structure_filter` (+ `ext_swing_len`) — **1d** structure interne/externe :
  2ᵉ analyse causale à pivots plus larges, gate de degré supérieur composé avec
  le HTF. Mesuré ❌ (OOS +108→+73 : coupe les retournements gagnants).
- `tp_measured_move` — **4b** symétrie de jambe : projection d'amplitude de la
  dernière jambe comme cible TP candidate (bracket). ⚠️ inerte en trailing, pire
  que le TP-liquidité en bracket.
- `inv_fvg_bonus` — **4c** inversion de rôle des FVG : bonus de confluence si un
  FVG opposé mitigé chevauche l'entrée. ≈ neutre (déjà couvert par les breakers).

La 4ᵉ piste (**5d** TP partiel / scale-out) est **abandonnée** : elle exigerait
un scale-out transverse du moteur d'exécution (backtest + live) pour une feature
mesurée négative (variance↓ mais PnL↓). Aucune modif de la config live (4h reste
trailing + time-stop conditionnel + sizing par confluence). 422 tests OK.

### ⚖️ smart_money : sizing pondéré par confluence (4h)

Empilé sur le trailing : au lieu d'un risque fixe par trade, on **alloue plus
aux setups à forte confluence** via le hook natif `size_factor` du
Backtester/live (« demi-Kelly ×confidence ») :

    size_factor = clip(1 + size_conf_slope × (score − size_conf_center), 0.4, 1.7)

Centré sur le score moyen (≈ 0.83 sur le 4h) → exposition globale ≈ inchangée :
c'est une RÉALLOCATION du risque, pas un cran de levier. Mesuré (vrai
Backtester, BTC 4h), empilé sur le trailing :

| Config | FULL 2018→26 | OOS 2024→26 | oos_score |
|---|---|---|---|
| + trailing 3.5×ATR | +318, PF 1.44, Sh 4.9 | +81, PF 1.34 | +0.291 |
| **+ sizing par confluence** | **+387, PF 1.51, Sh 5.2** | **+108, PF 1.46** | **+0.332** |

Amélioration MONOTONE avec la pente → le score du moteur est réellement
prédictif. Améliore tout sur les deux périodes **à exposition et DD égaux**
(−4,3 %) et **récupère le score composite OOS** que le trailing avait cédé
(0.332 > 0.327 du time-stop pur). Un 3ᵉ levier testé (taille scalée par régime)
est écarté : PnL en hausse mais Sharpe plat → exposition, pas edge.

`size_by_confluence: false` reste le défaut (byte-identique) ; le 4h l'active
via `optimizer_results`. Params `size_by_confluence`/`size_conf_slope`/
`size_conf_center` ajoutés au `param_space`. 418 tests OK.

---

## [12.12.0] - 2026-07-07

### 🏃 smart_money : trailing stop pour laisser courir les gagnants (4h)

Suite au time-stop (dont le gain en nombre de trades restait modeste), deux
idées testées pour exploiter davantage la volatilité :

1. **Trailing stop plutôt que time-stop** — au lieu du TP fixe, un stop suiveur
   à `trail_mult`×ATR (`TrailingStopManager` du Backtester) laisse **courir les
   gagnants**. Le time-stop devient **conditionnel** (`check_early_exit`) : après
   `time_stop_bars`, il ne coupe QUE les trades **stagnants** (MFE < `ts_profit_r`×R),
   jamais un gagnant qui court. On ride les tendances ET on coupe la chop.
2. **Plusieurs positions concurrentes** — pour ne pas rester bloqué sur un slot.

Mesuré via le **vrai Backtester** (chemin de prod, BTC 4h) :

| Config | FULL 2018→26 | OOS 2024→26 | oos_score |
|---|---|---|---|
| time-stop pur | +168, PF 1.23, Sh 2.9 | +84, PF 1.35 | +0.327 |
| **trailing 3.5×ATR + ts12** | **+318, PF 1.44, Sh 4.9** | +81, PF 1.34 | +0.291 |

**Idée 1 retenue** pour le 4h (`use_trailing: true`, `trail_mult: 3.5`,
`time_stop_bars: 12`) : le trailing **récupère l'upside des tendances** que le
time-stop pur sacrifiait (backtest complet quasi ×2, Sharpe 2.9→4.9) **sans
coûter au régime récent** (PnL OOS +81 vs +84 = égalité). Recul assumé : score
composite OOS un peu plus bas (0.291 vs 0.327), laisser courir = plus de vol.

**Idée 2 rejetée (mesurée pire)** : passer de 1 à 2+ positions n'ajoute ~+5 sur
le complet mais dégrade le régime récent (+21→+14, PF 1.34→1.21) ; le slot unique
filtre involontairement les signaux marginaux (espérance négative).

`use_trailing: false` reste le défaut de base (bracket fixe, validé toutes
périodes) ; nouveaux paramètres `use_trailing`/`trail_mult`/`ts_profit_r` ajoutés
au `param_space` de l'optimiseur. Backtest byte-identique avec le défaut (off).

### ⏱ smart_money : time-stop pour exploiter la volatilité choppy (4h)

Diagnostic depuis le Smart replay (« beaucoup de signaux, peu exploités ») :
sur le 4h récent, 37/45 sweeps sont contre-tendance (ignorés par design) ; le
vrai problème est que les trades pris **stagnent dans la chop** en attendant
une cible lointaine, ce qui bloque le slot de position unique et empêche de
capter les signaux suivants.

Cinq idées mesurées isolément (full + OOS) ; quatre échouent (entrées de
retournement sweep+CHoCH, breakeven/TP partiel, filtre ADX — toutes négatives
sur le régime récent). La gagnante : un **time-stop** (`time_stop_bars`, porté
par le mécanisme natif `exit_after_bars` du Backtester) qui coupe les positions
stagnantes après N barres. Sur le 4h :
- OOS 2024-2026 : **−19 → +96 USDC**, PF 1.08 → 1.41, DD −7,4 % → −4,0 % ;
- prend PLUS de trades (58 vs 51 : le slot se libère plus tôt) ;
- score OOS **doublé** (+0.354 vs +0.174), IS toujours positif, walk-forward
  consistance 40 % → 60 %.

Retenu pour le 4h (`optimizer_results`, choix utilisateur) — **pari de régime
assumé** : sacrifie le PnL des fortes tendances (backtest complet 400→206,
sous-période 2021-2024 négative), optimisé pour le régime choppy récent que la
méthodologie OOS du projet privilégie. `time_stop_bars: 0` reste le défaut de
base (validé toutes périodes) ; ajouté au `param_space` de l'optimiseur.
Backtest byte-identique avec le défaut (off). 415 tests OK.

### 🔧 SMC : correction des 10 findings de la revue de code

Suite à une revue complète (8 angles) de la branche, correction de tous les
findings vérifiés :

- **Cohérence UI/live** : `/api/scanner/smc` applique désormais l'overlay
  `optimizer_results` par timeframe (`resolve_strategy_params`) — la page Smart
  graph reflète la config RÉELLEMENT tradée (4h : min_score 0.75), au lieu des
  params de base.
- **Thread API protégé** : `/api/scanner/smc_replay` (backtest synchrone) est
  borné par un sémaphore non-bloquant (HTTP 429 si saturé) + cache court TTL,
  comme `/api/replay` — plus de risque d'affamer le bot live.
- **Mémoïsation de l'analyse** : `score()`/`trade_plans()`/`check_early_exit()`
  partagent un cache `(res, aux)` clé sur la dernière barre close ; le live ne
  relance plus `smc.analyze` (~130 ms → 0.3 ms entre deux barres identiques),
  et les endpoints ne l'exécutent plus qu'UNE fois par requête (vs 3).
- **Biais HTF aligné sur `_HTF_MAP`** (source unique de vérité d'app/live) :
  4h→1d au lieu d'un ×4 arbitraire ; ≥ aussi bon (4h : PF 1.52 vs 1.49) et
  cohérent partout (scanner, backtest, live). Défauts 4h re-validés :
  145 trades, WR 46.9 %, +40.1 %, PF 1.523, Sharpe 8.2.
- **Réutilisation des indicateurs canoniques** : `atr_wilder` ajouté à
  `indicators_core` (source unique du lissage Wilder, partagé SMC/Pine) ;
  `_vol_ratio_arr`/`_ema_arr` délèguent à `volume_ratio`/`ema` ;
  `_regression_channel` devient un wrapper de `regression_channel_at`.
- **Primitive de zones partagée** : `ZonesPrimitive` + palette extraites dans
  `base.html` (`SmcChart`), utilisées par Smart graph ET Smart replay (fin de
  la divergence de clipping entre les deux copies).
- **Factorisation** : helpers `_htf_ok`/`_dir_gate` partagés par `_signal_at`
  et `trade_plans` (garantit que les plans appliquent les mêmes filtres durs
  que le signal) + test de parité plan-immédiat ↔ `score()`.
- **Perf & nettoyage** : gardes CHoCH via tableau trié mémoïsé (fin du
  O(événements²) dans `prepare_for_backtest`) ; suppression de 2 clés mortes
  aux noms inversés dans `smc.DEFAULTS`.

Backtest 4h byte-identique vérifié après chaque refactor mécanique. Suite
complète : 414 tests OK (dont parité trade_plans/score et helpers).

---

## [12.11.0] - 2026-07-04

### ⏯ Page « Smart replay » : rejeu bougie par bougie de l'analyse SMC

Nouvelle page `/smartreplay` (menu Analyse) pour rejouer le cours et **voir
l'analyse évoluer comme le moteur la découvrait** : swings affichés à leur
confirmation seulement, zones qui naissent/se font toucher/s'invalident,
BOS/CHoCH et sweeps au fil de l'eau, trendlines recalculées à chaque barre.
Contrôles play/pause/pas-à-pas/vitesse (2→20 barres/s)/slider + raccourcis
clavier. Les **trades sont ceux du vrai Backtester** (paramètres par TF
résolus) : bracket entrée/SL/TP visible pendant la position (PnL latent),
dénouements ✓ TP / ✗ SL, et panneaux Performance cumulée / Journal des
trades / Lecture à la barre — l'outil d'évaluation de pertinence des
configurations. Architecture : UNE requête `/api/scanner/smc_replay`
précalcule tout (le moteur causal expose les indices de cycle de vie de
chaque entité), le navigateur reconstruit l'état à n'importe quelle barre —
lecture fluide et scrubbing instantané sans appel serveur. Vérifié par
navigation Playwright (chargement, saut, lecture auto, captures).

### 🧭 SMC : biais multi-timeframe, volume profile, killzones, AMD, rejection blocks — mesurés un par un

Cinq nouveaux enrichissements implémentés dans le moteur (`app/core/smc.py`)
et la stratégie `smart_money`, puis **mesurés ISOLÉMENT** sur BTC/USDC
15m→1d (historique complet + dernier tiers pseudo-OOS 2024-2026) :

- **Biais multi-timeframe** (`htf_trend_series` : structure BOS/CHoCH sur
  buckets horloge ×4, mapping causal par barre) — ✅ **seul enrichissement
  gagnant sur tous les TF**, activé par défaut (`htf_filter: soft`).
  4h : PF 1.485 vs 1.414, DD −8.0 vs −9.9, OOS +23 vs −8 ; **la période
  2024-2026 repasse positive (PF 1.03)**. Nouveaux défauts 4h :
  161 trades, WR 46.6 %, +41.0 %, Sharpe 7.5.
- **Volume profile** (`volume_profile` : POC/HVN/LVN causals) — confluence
  neutre au seuil 0.55, cibles légèrement négatives → off par défaut,
  exploités par les configs par TF à seuils élevés.
- **Killzones/sessions** (LDN 07-10, NY 12-15 UTC) — aucun edge horaire
  mesurable sur BTC 24/7 → off (bonus et filtre).
- **AMD / Power of Three** (compression → sweep de manipulation) — bonus
  neutre au seuil 0.55 → off, exploré par l'optimiseur.
- **Rejection blocks** (mèches de swing ≥ 0.5×ATR, setup REJECTION_RETEST)
  — dilue le PF global mais améliore l'OOS 4h/2h → off, exploré par
  l'optimiseur.

Calibration PAR TIMEFRAME (grille IS 2/3 / OOS 1/3, sélection sur le PF OOS,
score officiel `composite_score` du repo) écrite dans
`strategies/smart_money.yaml` → `optimizer_results`. Page Smart graph :
calques « Rejection blocks » et « Volume profile », biais HTF et session
dans la Lecture du marché. 35 tests SMC, suite complète 412 OK.

### 📌 Smart graph : tableau « Trades à ouvrir » (plans recommandés)

Nouvelle méthode `Strategy.trade_plans()` (smart_money) exposée via
`/api/scanner/smc` : en plus du signal immédiat, elle anticipe les setups EN
ATTENTE — retests des order blocks frais et sweeps potentiels des poches de
liquidité actives, avec les mêmes filtres durs que la stratégie (tendance,
côté momentum, EMA200, gain > 0,4 %, RR minimal). La page Smart graph affiche
ces plans dans un tableau : statut (⚡ maintenant / ⏳ en attente), sens,
setup, **déclencheur à attendre**, entrée/SL/TP recommandés, gain potentiel,
RR, distance au prix et score minimum ; un clic trace les niveaux du plan sur
le graphique et détaille le motif. 30 tests SMC (contrat des plans inclus).

### 💡 Page « Smart graph » + enrichissements SMC (voids, breakers, structure, cycle)

Nouvelle page dédiée **`/smartgraph`** (menu Analyse → Smart graph) : chart
d'analyste complet façon « pro trader » — zones en **vrais rectangles ombrés**
(primitive canvas lightweight-charts) pour l'offre/demande, les poches de
liquidité BSL/SSL, les FVG, les liquidity voids et les breakers ; **zigzag de
structure** (peaks/troughs) avec labels HH/HL/LH/LL et flèches BOS/CHoCH ;
trendlines + canal de régression ; **projection de cycle** (expected
peak/trough à la borne du canal) ; price lines entrée/SL/TP du signal courant.
Calques activables, deep-link `?symbol=…&tf=…`, panneaux « Lecture du
marché », « Signal smart_money » et « Zones actives ».

Moteur `app/core/smc.py` enrichi :
- **Liquidity Voids** : runs de ≥3 bougies directionnelles traversant
  ≥2.5×ATR, cycle open → mitigated → filled — bords opposés utilisés comme
  cibles de TP par la stratégie (`use_void_targets`, **+65 USDC** sur BTC 4h) ;
- **Breaker Blocks** : OB invalidé → polarité inversée, retest suivi ;
- **Structure line (zigzag)** : polyligne causale des swings alternés ;
- **Cycle de marché** : phase advance/decline + cible projetée sur le canal ;
- helpers causaux `trendline_value_at`, `regression_channel_at`,
  `void_targets_above/below`, zone morte ±eq_tol×ATR sur les sweeps de swings.

Stratégie `smart_money` adaptée : cibles voids, confluence « tap de
trendline » (+0.05), setup `BREAKER_RETEST` (testé négatif sur BTC 4h :
−163 USDC / 220 trades → off par défaut, exploré par l'optimiseur).
**Validation BTC/USDC 4h 2018→2026 : 181 trades, WR 46,4 %, +40,5 %, PF 1.41,
Sharpe 6.5, DD −9,9 %** (PF par tiers 2.28/1.54/0.95). Page vérifiée par
capture navigateur (Playwright). 28 tests SMC — suite complète 405 OK.

### 🧠 Moteur d'analyse Smart Money Concepts + stratégie `smart_money`

Nouveau moteur `app/core/smc.py` (une passe causale O(n), sans lookahead) :
- **Structure de marché** : swings fractals HH/HL/LH/LL, cassures **BOS** et
  changements de caractère **CHoCH** (sur clôture) ;
- **Zones de liquidité** : equal highs/lows (buy-side/sell-side liquidity),
  cycle de vie active → swept, **sweeps** (stop hunts) avec détection du rejet ;
- **Offre/Demande** : **order blocks** par displacement (corps ≥ k×ATR),
  strength 2 si l'impulsion casse la structure, statut fresh/touché/invalidé ;
- **FVG** (imbalances) ouverts/mitigés/comblés ;
- **Premium/Discount** (équilibre 50 %, OTE 62-79 %) — version causale par barre ;
- **Tendances** : trendlines automatiques (2 derniers swings) + canal de
  régression linéaire ±2σ.

Nouvelle stratégie **`smart_money`** (`strategies/smart_money.yaml`) :
- setups **SWEEP_REVERSAL** (prise de liquidité rejetée) et **OB_RETEST**
  (premier retour dans l'order block) — uniquement avec la tendance, côté
  momentum du range, au-dessus/en-dessous de l'EMA200 ;
- TP posé devant la prochaine poche de liquidité opposée, bracket fixe ;
- ⚠ **filtre de gain : seules les positions au gain potentiel > 0,4 %**
  (`min_gain_pct`) et RR ≥ 1.2 sont retenues ;
- backtest O(1)/barre via `prepare_for_backtest` (une passe + cache).

Validation BTC/USDC 4h 2018→2026 : 179 trades, WR 45,2 %, **+33,8 %**, PF 1.35,
Sharpe 5.5, DD −10,6 % (PF par tiers : 2.33/1.40/0.87 — edge décroissant sur
2024-2026 ; TF < 4h négatifs avec les défauts, laissés à l'optimiseur).

Branchements : case **« SMC (Smart Money) »** sur le graphique du scanner
(zones, markers BOS/CHoCH/sweeps, trendlines, canal, signal courant avec
entrée/SL/TP/gain %) via `GET /api/scanner/smc` ; stratégie disponible dans le
replay, le backtest, l'optimiseur et le live. Documentation :
`docs/SMART_MONEY_CONCEPTS.md`. 22 tests unitaires (`tests/test_smc.py`).

---

## [12.10.0] - 2026-06-18

### ⚡ Performance backtest/optimisation : suppression d'un O(n²) et du « get_column storm » des stratégies ML

**Symptôme.** Backtests ML très lents et **ralentissant avec la taille** (279 → 185
→ 147 bars/s en cours de route) — catastrophique sur 50 000 barres.

**Causes (profil cProfile).**
1. **`_detect_timeframe(df)` appelé à chaque barre** sur la fenêtre **croissante**
   `df[:i+1]` : `times.diff().median()` est O(i) → backtest O(n²). Or le
   timeframe est **constant** sur tout le backtest.
2. **`_predict` (stratégies polars v7/v10_retrained/v11/v11_followsetup,
   opus_stat_retrained_v4) accédait aux ~440 colonnes de features une par une**
   (`row[c][0]`), soit ~880 `get_column` polars par barre (~58 % du temps de
   backtest).

**Correctif.**
- `_detect_timeframe` ne lit plus que les **derniers deltas** (`tail(64)`) → O(1)
  par appel, résultat identique (espacement uniforme). Appliqué à toutes les
  variantes (ML + `_no_ml`).
- `_predict` extrait la dernière ligne en **un seul appel** (`row(0, named=True)`
  → dict) puis lit les features depuis le dict → fini le `get_column` par feature.
- Résultats **numériquement identiques** (336 tests OK, dont parité scoring/ML).

**Gain mesuré** (v11, 1h) : **~5× plus rapide** (≈380 → ≈1840 bars/s à 4 000
barres) et **mise à l'échelle linéaire** restaurée (plus de ralentissement ; le
résidu provient des seuls ré-entraînements walk-forward périodiques).

**Audit O(n²) de TOUTES les stratégies** (40 fichiers) — 5 autres bugs corrigés,
même cause racine (indicateur pleine fenêtre recalculé par barre, sans cache ni
tail, pour ne lire que la dernière valeur) :
- `composite_score` : FFT+polyfit sur toute la fenêtre/barre (bornée à 1024
  barres) **et** stochastique recalculé sur tout le df (borné à k+d barres).
- `harmonic_regime` : `.to_numpy()` sur la colonne entière (ATR/close) pour
  n'utiliser que la queue → matérialisation bornée (`tail`), `_cycle` reçoit la
  queue + l'index absolu (cache stride préservé).
- `fear_momentum` : `volume.rolling_mean(20)[-1]` sur tout le df → `tail(20)`.
- `ml_dynamic_threshold` : `_detect_tf` (deltas pleine fenêtre) et `_adx(df,14)`
  recalculé par barre → bornés (`tail(64)` / `tail(300)`).

Mise à l'échelle vérifiée **constante** après correctif (ex. composite_score :
~1050 bars/s à 4 000 comme à 12 000 barres). 35 stratégies déjà saines (cache
`prepare_for_backtest` ou lectures `pre_val`/tails bornées). 336 tests OK.

### 🎯 Optimiseur : score monotone, données auto-dimensionnées, recherche TPE, parallélisme par défaut, réglage ML two-phase

Cinq améliorations ciblées de l'optimiseur et de ses performances :

- **#1 — Score composite monotone avec le PnL** (`opt_scoring.py`). Avant, la
  pénalité d'un résultat perdant était *multiplicative et jamais négative*
  (`ret_sign ∈ {1.0, 0.3}`) : une stratégie **nette perdante** au win-rate/Sharpe
  corrects obtenait un score **positif**, était sélectionnée par l'optimiseur et
  passait le gate live (`MIN_VIABLE_SCORE`). Désormais : `PnL > 0` → bundle
  qualité (échelle **inchangée**, rétro-compatible avec les scores déjà
  persistés) ; `PnL ≤ 0` → score = rendement normalisé (**négatif** et monotone
  avec la perte). Impact live : les paramétrages nets perdants sont correctement
  exclus de `get_active_strategies_per_tf` (effet uniquement après ré-optimisation
  — les scores déjà sauvegardés restent figés).
- **#2 — Limite de bougies auto-dimensionnée** (`optimizer.auto_fetch_limit`,
  utilisée par `optimize_runner.py` et la route web). Corrige le décalage
  `RECOMMENDED_LIMIT[1h]=1500 < 2229` requis par les omnibus ML, qui faisait
  **ignorer silencieusement** ces stratégies. La limite par défaut (`--limit 0`,
  `limit=0` côté UI) dérive désormais du besoin réel (`ceil(min_bars/0.35)`). Les
  jobs ignorés sont en plus **remontés visiblement** (`⊘ … ignoré : …`) au lieu
  d'être noyés dans les logs.
- **#4 — Recherche bayésienne TPE via Optuna** (`optimizer.bayesian_search`).
  Remplace l'heuristique « random + perturbation locale » par une vraie recherche
  informée (TPE). Parallèle sur un **ProcessPool persistant** (cache de features /
  d'entraînement réutilisé entre lots) ou séquentielle (cache in-process chaud).
  **Repli automatique** sur l'ancienne heuristique si Optuna est absent (dépendance
  optionnelle ajoutée à `requirements.txt`).
- **#5 — Parallélisme par défaut** (`optimize_runner.py --jobs 0 = auto cpu-1`).
  Les threads BLAS/LightGBM restent épinglés à 1 et le portillon mémoire borne la
  concurrence → discret mais bien plus rapide.
- **#6 — Réglage ML two-phase** (`optimizer.optimize_two_phase`, opt-in via
  `--ml-tune` / case UI). Grille externe sur les hyperparamètres d'entraînement
  (`learning_rate`, `n_estimators`) × recherche interne sur les seuils de décision.
  Chaque combo segmente le cache d'entraînement (coût ~linéaire). Les HP retenus
  sont persistés dans `best_params` et réutilisés au ré-entraînement du modèle final.

---

## [12.9.0] - 2026-06-16

### 🛡️ Optimiseur ML : portillon mémoire anti-OOM (corrige l'arrêt silencieux du bot pendant une optimisation multi-jobs)

**Problème.** Lancer une optimisation ML sur plusieurs stratégies × timeframes
(ex. 9 jobs) depuis l'UI faisait **s'arrêter le bot sans aucune traceback**
(retour sec au prompt). Cause : `start_async` ouvre un thread par job et en
laisse tourner jusqu'à `cpu-1` **en parallèle** ; avec le défaut `n_jobs=1`,
chaque job évalue ses trials **dans le process** (pas de ProcessPool) — soit
`cpu-1` backtests ML walk-forward simultanés, chacun **réentraînant LightGBM en
boucle** sur de larges matrices de features. Sur de gros jeux de données (50k
bougies), le pic mémoire cumulé épuise la RAM → `std::bad_alloc` LightGBM / OOM →
le process entier est tué (sur Windows : pas d'OOM-killer noyau, pas de
traceback). Le garde-fou `mem_aware_max_workers` existant ne couvrait que le
ProcessPool d'un job (chemin `n_jobs>1`), **jamais** la concurrence inter-jobs,
et était de surcroît **désactivé sur Windows** (`available_memory_bytes()`
dépendait de `psutil` — non installé — ou de `/proc/meminfo` — Linux only).

**Correctif.**
- **Portillon d'admission mémoire** (`auto_optimizer.py`) : un job n'entre en
  exécution que si son empreinte estimée tient dans le budget restant (70 % de
  la RAM dispo, mesurée par lot). La mémoire **cumulée** des jobs actifs est
  bornée → sur machine contrainte, les jobs ML se sérialisent au lieu d'OOM.
  Règle anti-blocage : un job seul est toujours admis (au pire, un par un).
  L'estimation est échelonnée sur la taille réelle des données (la variable qui
  provoque l'OOM) et le type de stratégie (ML vs non-ML).
- **`available_memory_bytes()` multi-plateforme** (`opt_workers.py`) : ajout du
  fallback Windows `GlobalMemoryStatusEx` (ctypes) → le cap mémoire fonctionne
  enfin sur Windows même sans `psutil`.
- `psutil` ajouté aux dépendances (chemin préféré ; fallbacks `/proc/meminfo` et
  `GlobalMemoryStatusEx` conservés s'il est absent).
- `optimize_runner.py --ml-only` : pendant symétrique de `--no-ml-only`, optimise
  toutes les stratégies ML (détection structurelle `BaseStrategyML`). Ex. :
  `python optimize_runner.py --ml-only --limit 50000 --apply` (séquentiel, sûr).
- Couverture : `tests/test_opt_mem_gate.py` (admission, mise en attente,
  anti-blocage, annulation, libération, estimation).

### ⚡ Optimiseur ML : hyperparamètres d'entraînement figés + fenêtres alignées (≈ heures → minutes sur 50k bougies)

**Problème.** Impossible de mener à terme les optimisations des stratégies ML
sur 50 000 bougies (plusieurs heures par job, souvent interrompu) :

1. Les `param_space` des stratégies ML « retrained » échantillonnaient les
   hyperparamètres **d'entraînement** (`retrain_every`, `warmup_bars`,
   `n_estimators`, `num_leaves`, `learning_rate`, `amp_top_pct`, et pour V11
   `label_horizons`/`calibrate`/`prune_features`). Chaque trial avait donc une
   clé de cache d'entraînement différente → le cache process-wide
   (`train_cache.py`) ne servait à rien et **chaque trial repayait l'intégralité
   des retrains LightGBM walk-forward** (~30-100 entraînements × 2 modèles ×
   ~462 features par trial).
2. Même à hyperparamètres identiques, le déclenchement des retrains dépend du
   compteur d'appels `score()` (fonction des trades du trial) : les fenêtres
   d'entraînement divergeaient de quelques barres entre trials → fingerprints
   différents → cache contourné.

**Correctif.**
- Les hyperparamètres d'entraînement sortent de `param_space` (déplacés en
  `fixed_params`, valeurs = `_DEFAULTS`, surchargables via le YAML stratégie)
  pour : `opus_omnibus_v7`, `opus_stat_retrained_v4`, `opus_omnibus_v10_retrained`,
  `opus_omnibus_v11`, `opus_omnibus_v11_followsetup`, `opus_omnibus_v12`
  (`mldyn_lookahead`/`mldyn_vol_multiplier`), `scoring_statistique_opus_v3/v4/v5`.
  L'optimiseur se concentre sur les paramètres de **décision** (seuils, SL/TP…),
  ceux qui déterminent réellement le ratio gain/risque par timeframe.
- Nouveau `aligned_train_window()` (`app/core/train_cache.py`) : la fin de la
  fenêtre d'entraînement est alignée sur la grille `retrain_every` → fenêtres
  identiques entre trials → hits de cache déterministes. Staleness bornée à
  `retrain_every` barres, identique à la cadence de déclenchement existante.
- **Bugfix walk-forward** : dans `_train_impl`, le fast-path features prenait
  `self._bt_features.head(len(train_df))` — soit les **premières** lignes des
  features pré-calculées alors que `train_df` est une tranche de **fin** de
  fenêtre. Les modèles inline s'entraînaient donc sur les plus *vieilles*
  données au lieu des plus récentes. Corrigé via un offset (`_bt_train_offset`)
  posé par `score()` avant `_train`.

Vérifié sur données synthétiques (3 000 barres 1h, `opus_omnibus_v7`) : 2ᵉ run
= 100 % de hits du cache d'entraînement, 0 réentraînement, trades identiques.

---

## [12.8.0] - 2026-06-08

### 🗑️ Suppression des stratégies ML `_1`

`opus_omnibus_v7_1`, `v8_1`, `v9_1`, `v10_1` (variantes « score additif » des
omnibus ML) sont supprimées (`.py` + `strategies/*.yaml`). Aucune référence
restante dans `config.yaml`, les tests ou l'UI.

### 🛠️ Optimiseur : score réaligné sur le PnL et garde-fou d'application durci

**Problème.** L'optimiseur pouvait retenir/appliquer un paramétrage au PnL OOS
nettement inférieur (ex. `+33` sur 3 trades) face au paramétrage courant
(`+96` sur 15 trades). Deux causes :

1. **Score composite dominé par un Sharpe brut non plafonné** (`_composite_score`,
   `optimizer.py`). Le terme `sharpe * 0.28` n'étant pas borné, un Sharpe absurde
   de petite fenêtre (>50, voire 120 sur 3 trades) écrasait tous les autres
   termes (échelle 0–1) — et le **montant** du PnL n'entrait pas du tout dans le
   score (seul son *signe* via `pnl_sign`). Le score gonflait jusqu'à ~34 alors
   que l'UI attend une échelle 0–1 (seuils verts `>0.4`, viabilité `-0.05`).
2. **Garde-fou d'application « 2 critères sur 3 »** (`_beats_baseline`,
   `auto_optimizer.py`) : un meilleur Win Rate **et** Sharpe suffisait à
   appliquer un PnL pourtant inférieur au baseline.

**Correctif.**
- Sharpe **normalisé** dans `[-1, 1]` (saturation à `|Sharpe| ≥ 10`) pour qu'il
  pèse comme les autres métriques ; ajout d'un terme de **montant du PnL**
  normalisé (poids 0.20) ; léger renfort du poids du nombre de trades
  (`0.08 → 0.10`). Le score retrouve l'échelle 0–1 attendue par l'UI.
- `_beats_baseline` rend l'**amélioration du PnL OOS obligatoire** (plus jamais
  outvotée par WR/Sharpe), en plus d'au moins un gain de qualité (WR ou Sharpe).

### 🛠️ Optimiseur : « non appliqué = non utilisé » + `params:` jamais écrasé

**Problème.** Un résultat d'optimisation **non appliqué** (refusé par le
garde-fou en auto-apply, ou simplement en attente du bouton « Appliquer »)
était quand même persisté dans `optimizer_results[tf]` via
`save_optimizer_results`. Or `resolve_strategy_params` donne **précédence** à
`optimizer_results` sur `params:`, et `load_config` recharge ce store : le
paramétrage « refusé » devenait donc **immédiatement actif** pour le
backtest/comparatif/live (et pouvait même activer une stratégie en live). C'est
pourquoi un comparatif après optimisation reproduisait les chiffres « Après
optimisation » alors que « rien n'était appliqué ». De plus, le panneau
« Avant optimisation » était calculé sans timeframe → sans l'overlay
`optimizer_results`, donc à partir du seul bloc `params:`, ce qui ne reflétait
pas le paramétrage réellement actif.

**Correctif.**
- **Non appliqué = non utilisé** : les chemins non appliqués tracent désormais
  le résultat dans le changelog (audit) via la nouvelle fonction
  `record_optimizer_audit`, **sans** écrire dans `optimizer_results`. Le
  paramétrage en place reste actif tant qu'il n'est pas explicitement appliqué.
- **`params:` jamais écrasé** : `apply_best_params` écrit désormais
  **uniquement** dans `optimizer_results[tf]` (le store actif, par précédence) et
  laisse intact le bloc `params:` = configuration par défaut réglée à la main.
  Un timeframe est requis (sans lui, aucun emplacement à activer).
- **Baseline réaligné** : `_run_baseline` reçoit le timeframe et applique
  l'overlay `optimizer_results` → le panneau « Avant optimisation » reflète le
  paramétrage réellement actif (comme le live et le comparatif).

### 🐛 Optimiseur : Alpha OOS manquant dans le panneau « Après optimisation »

Le chemin d'évaluation **non parallèle** (`Optimizer._eval`) n'incluait pas
`oos_alpha` dans son dict de résultat, contrairement au worker parallèle. En
mode `n_jobs=1`, `best_oos_alpha` revenait donc `None` et l'UI masquait la ligne
Alpha. Ajout de `oos_alpha` à `_eval` pour aligner les deux chemins.

---

## [12.7.0] - 2026-06-06

### ✨ Indicateurs du catalogue V4 ajoutés à `indicators.py` + runner d'optimisation

**Indicateurs repris du catalogue de features V4 (~462 colonnes).** Quatre
primitives génériques, réutilisables et jusque-là absentes de
`app/core/indicators.py`, y sont ajoutées (avec tests) :
- `roc(close, n)` — Rate of Change en % (momentum fondamental, étonnamment absent) ;
- `green_ratio(df, n)` — proportion de bougies haussières sur `n` (breadth locale) ;
- `rsi_divergence(df, period, lookback)` — divergence RSI/prix signée {−1, 0, +1}
  (fusion des features `bull_div`/`bear_div`) ;
- `trend_duration(df, n, adx_threshold)` — barres consécutives en tendance forte
  (persistance de régime).

Les autres features V4 utiles étaient déjà couvertes : `precompute_df` expose en
O(1) RSI/ATR/ADX/±DI/MACD/SMA/EMA, les ratios de volatilité normalisés 100b
(`_pre_atr_pct_r`…), `_pre_range_pos20`, `_pre_rsi_vel6`, structure de bougie, etc.
— c'est cette base que consomment les jumeaux `_no_ml`.

**Runner d'optimisation en ligne de commande — `optimize_runner.py`.** Lance
l'optimisation des stratégies **une à une** sans passer par l'interface web
(même moteur `AutoOptimizer` : baseline → recherche → sauvegarde
`strategies/<nom>.yaml`, ré-entraînement/persistance du modèle pour les ML) :
- **séquentiel** (un seul job à la fois) via `AutoOptimizer.optimize_sequential` ;
- **anti-veille** multi-plateforme (macOS `caffeinate` / Windows
  `SetThreadExecutionState` / Linux `systemd-inhibit`), best-effort ;
- **thread-safe** : verrou fichier exclusif (une seule instance) en plus des
  verrous internes de l'optimiseur (YAML, registre de jobs) ;
- **tâche de fond discrète** : priorité processus abaissée (`nice`/IDLE) et threads
  de calcul bornés (`--jobs`, défaut 1 ; env `OMP/MKL/…_NUM_THREADS` plafonnés).

Exemples : `python optimize_runner.py --no-ml-only --apply`,
`python optimize_runner.py --strategies opus_omnibus_v11_no_ml --tfs 1h --trials 30`.

### 🔧 Fichiers

| Fichier | Changement |
|---------|------------|
| `app/core/indicators.py` | + `roc`, `green_ratio`, `rsi_divergence`, `trend_duration` |
| `app/engine/auto_optimizer.py` | + `AutoOptimizer.optimize_sequential` (exécution une-à-une) |
| `optimize_runner.py` | Script CLI : optimisation séquentielle, anti-veille, verrou, priorité basse (nouveau) |
| `tests/test_indicators.py` | + tests des 4 nouveaux indicateurs |

---

## [12.6.0] - 2026-06-06

### ✨ Jumeaux « sans ML » des stratégies Opus Omnibus + seuil dynamique

Les stratégies ML (`opus_omnibus_v8/v10/v11/v11_followsetup`, `ml_dynamic_threshold`)
sont coûteuses à entraîner et à maintenir (modèles LightGBM/sklearn, pkl,
ré-entraînement périodique). Cette version ajoute pour chacune un **équivalent
purement à base d'indicateurs**, suffixé `_no_ml`, qui réplique le routing de la
stratégie d'origine et ne remplace **que** les deux sorties ML.

**Chaque jumeau est autonome.** Aucun module proxy partagé, aucun import croisé
entre stratégies : tout le routing (régime, setups, sélection, sorties
anticipées) est embarqué dans le fichier, et tous les indicateurs proviennent de
`app/core/indicators.py`.

**Performance.** Les proxys lisent les indicateurs en **O(1)** depuis les
colonnes `_pre_*` déjà calculées par `precompute_df` (appliqué une fois par le
backtest et le live ; repli idempotent sinon). Aucun DataFrame de features lourd
(~462 colonnes) n'est reconstruit par bougie → backtest ~0.5 s (contre plusieurs
dizaines de secondes auparavant), coût live négligeable.

**Proxys déterministes (inline dans chaque fichier) :**
- `p_up` (direction) — sigmoïde d'une moyenne pondérée de signaux directionnels :
  DI_diff, RSI, MACD/ATR, ROC, distance SMA50, vélocité RSI, position dans la
  range 20, direction du corps de bougie.
- `p_event` (amplitude) — sigmoïde recentrée de signaux d'amplitude déjà
  normalisés par leur moyenne 100 barres (TF-indépendants) : ATR%, range,
  écart-type des log-returns, ratio de volume, ADX, corps absolu.
- Coefficients (`p_up_gain`, `p_event_gain`, `p_event_center`) paramétrables/optimisables.

**Variantes ajoutées :** `opus_omnibus_v8_no_ml`, `opus_omnibus_v10_no_ml`,
`opus_omnibus_v11_no_ml` (régime enrichi DI/pente conservé),
`opus_omnibus_v11_followsetup_no_ml` (sortie sur flip de setup conservée),
`ml_dynamic_threshold_no_ml` (filtre ADX + seuils proba + porte de volatilité
reproduisant l'esprit « seuil dynamique »).

### 🔧 Fichiers

| Fichier | Changement |
|---------|------------|
| `app/strategies/opus_omnibus_v8_no_ml.py` | Jumeau autonome sans ML de V8 (nouveau) |
| `app/strategies/opus_omnibus_v10_no_ml.py` | Jumeau autonome sans ML de V10 (nouveau) |
| `app/strategies/opus_omnibus_v11_no_ml.py` | Jumeau autonome sans ML de V11 (nouveau) |
| `app/strategies/opus_omnibus_v11_followsetup_no_ml.py` | Jumeau autonome sans ML de V11-FollowSetup (nouveau) |
| `app/strategies/ml_dynamic_threshold_no_ml.py` | Jumeau autonome sans ML du seuil dynamique (nouveau) |
| `strategies/*_no_ml.yaml` | Configs (params + coefficients de proxy) des 5 jumeaux |

---

## [12.5.0] - 2026-06-03

### ✨ Dérivés « au fil de l'eau » + stratégie `funding_flow` (100 % dérivés)

Suite de l'intégration dérivés (V12.4) : accumulation automatique dans la boucle
live + stratégie directionnelle théorique exploitant funding/OI/LSR/taker.

**Accumulation au fil de l'eau (comme l'OHLCV) :**
- `DerivativesStore.refresh()` — fetch incrémental throttlé, merge dans
  `data/derivatives/*.parquet` (même logique que CandleStore pour l'OHLCV).
- Branché dans `OHLCVCache.get()` derrière le flag `derivatives.enabled`
  (opt-in, **comportement inchangé si désactivé**) : à chaque nouvelle bougie,
  accumulation + injection des colonnes `funding_z`/`oi_change_pct`/`lsr_z`/
  `taker_z` dans le df de scoring. **Gracieux** : réseau KO → df OHLCV inchangé.
- `research/accumulate_derivatives.py` — accumulation hors-bot (cron/backfill).
- Config : section `derivatives` (`enabled`, `period`, `refresh_interval`, `z_window`).

**Stratégie `funding_flow` (rule-based, théorique) :** fade des extrêmes de
positionnement — pression de foule = somme pondérée `funding_z`/`lsr_z`/`taker_z`
(contrarian), conviction renforcée par l'OI, garde-fou tendance. Pression positive
extrême (foule longue) → SHORT ; négative → LONG. Sans dérivés → abstention.
⚠️ Théorique (historique gratuit OI/LSR/taker ≈ 30 j) : à calibrer/valider en live.

### 🔧 Fichiers

| Fichier | Changement |
|---------|------------|
| `app/core/derivatives.py` | + `refresh()` throttlé (accumulation au fil de l'eau) |
| `app/live/ohlcv_cache.py` | + enrichissement dérivés dans `get()` (opt-in, gracieux) |
| `app/core/config.py`, `config.yaml` | + section `derivatives` |
| `app/strategies/funding_flow.py` | Stratégie directionnelle 100 % dérivés |
| `strategies/funding_flow.yaml` | Paramètres |
| `research/accumulate_derivatives.py` | Script d'accumulation/backfill autonome |
| `tests/test_funding_flow.py` | Tests stratégie + hook OHLCVCache (réseau mocké) |

---

## [12.4.0] - 2026-06-03

### ✨ Intégration de données de dérivés (gratuites) + edge directionnel

Réponse à la question « peut-on prédire la direction ? ». Démarche en deux temps.

**1. Chasse à l'edge directionnel** (`research/directional_hunt.py`) — mesure
honnête sur OHLCV (P(up|condition), z-scores binomiaux, AUC logistique OOS) :
- La direction non-conditionnelle est ~martingale (AUC OOS combiné ≈ **0.52**).
- **Seul edge robuste : la mean-reversion sur la position-dans-le-range** —
  bas de range → biais UP (P(up)≈**57 %**, **z=7.6**), haut → DOWN (z=-7.3),
  cohérent 1h/4h/1d. Renforcé par : reversal de streaks, fade d'euphorie, rebond
  de capitulation (volume), pullback en tendance.
- Saisonnalité (heures funding, jour de semaine) : **démentie** (z≈0).

**2. Pourquoi il faut les dérivés** — le cœur OHLCV mean-reversion est ≈ breakeven
(`derivatives_reversion` backtest 4h ≈ -1.8 %) : l'edge directionnel est réel
(win 59 %) mais le payoff est asymétrique (cassures de range → besoin de 71 % de
win). **Filtrer les fausses reversions exige funding/OI/sentiment** — là vit
l'alpha directionnel crypto.

**Module `app/core/derivatives.py` — `DerivativesStore` (gratuit, sans clé API) :**
- funding_rate (ccxt, historique long), open_interest (ccxt), long_short_ratio &
  taker_buy_sell_ratio (Binance futures-data REST). Cache Parquet, thread-safe,
  **dégradation gracieuse** (aucune exception si réseau KO).
- `align_to_ohlcv()` : enrichit l'OHLCV (join_asof causal) avec funding_z, oi_change,
  lsr_z, taker_z. Câblage live en 1 ligne (cf. research/DERIVATIVES_integration.md).

**Stratégie `derivatives_reversion` (rule-based, zéro ML) :** fade des extrêmes de
range, **veto/boost par funding & sentiment** quand les colonnes sont présentes ;
fallback OHLCV pur sinon.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/core/derivatives.py` | DerivativesStore (funding/OI/LS/taker, cache, alignement) |
| `app/strategies/derivatives_reversion.py` | Stratégie directionnelle mean-reversion + dérivés |
| `strategies/derivatives_reversion.yaml` | Paramètres |
| `tests/test_derivatives.py` | Tests (réseau mocké) du store + de la stratégie |
| `research/directional_hunt.py` | Chasse à l'edge directionnel (P(up), AUC OOS) |
| `research/DERIVATIVES_integration.md` | Doc : sources gratuites, limites, câblage |
| `research/backtest_reversion.py` | Harnais backtest du cœur OHLCV |

---

## [12.3.0] - 2026-06-03

### ✨ Nouvelle stratégie — `volatility_squeeze` (RULE-BASED, antithèse de l'Omnibus)

Issue d'une **remise en question des hypothèses** de la lignée `opus_omnibus`
V7/V8/V10/V11 (cf. `research/CRITIQUE_omnibus_v7-v11.md`).

**Critique de l'Omnibus :**
- Hypothèse fondatrice fausse — la lignée prédit la DIRECTION par ML alors qu'elle
  **admet elle-même un AUC_dir ≈ 0.53** (quasi-aléatoire, docstring V10). Toute la
  machinerie de routing `p_up` filtre donc du bruit.
- Sous-exploite le seul edge réel — l'AMPLITUDE/volatilité (AUC ≈ 0.7, clustering
  ACF|r| 0.15-0.28).
- Sur-apprentissage — 17 à 23 paramètres + seuils par setup, **tunés sur des
  backtests in-sample de 12-122 trades**, `oos_score: null` (aucune validation OOS).
- Mauvais timeframes — 15m/30m/1h, **sous le mur des frais** (mesuré).
- Complexité fragile — LightGBM inline, path-dépendant, non déterministe.

**Réponse — `volatility_squeeze` :** trader la VOLATILITÉ (prévisible), pas la
direction (aléatoire). On attend une **compression** (squeeze = largeur Bollinger
dans son percentile bas) puis sa **détente alignée sur la tendance établie**
(jamais prédite) ; abstention en chop. Règle pure : **~8 paramètres, déterministe,
ZÉRO ML, zéro réentraînement**.

**Backtest 4h, 7.5 ans (frais/spread/borrow réalistes) :**
- long-only strict : **+68.7 %** · Sharpe 13.7 · maxDD **-5.8 %** · **PF 2.49** · win 51 %.
- Walk-forward OOS : **consistance 80 %** (meilleure des stratégies du repo).
- BEAR 2022 **-1.2 % vs B&H -53 %** ; BULL 2023-24 +12.0 % (PF 3.9) ; CHOP -1.2 %.
- ⚠️ 1h backtesté **-41.6 %** → confirme que l'orientation bas-TF de l'Omnibus est
  sous le mur des frais.

> 8 paramètres déterministes battent 23 paramètres + ML inline non-validé OOS :
> la discipline (ne trader que l'edge réel) bat la complexité.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/strategies/volatility_squeeze.py` | Stratégie rule-based (`BaseStrategy`, zéro ML) |
| `strategies/volatility_squeeze.yaml` | Params + `optimizer_results` (4h, 1d) |
| `tests/test_volatility_squeeze.py` | Tests unitaires + intégration |
| `research/CRITIQUE_omnibus_v7-v11.md` | Critique structurée de la lignée Omnibus |
| `research/backtest_squeeze.py` | Harnais backtest/walk-forward/split |

---

## [12.2.0] - 2026-06-03

### ✨ Nouvelle stratégie — `momentum_blitz` (AGRESSIVE, plein capital)

Pendant **agressif** de `harmonic_regime` : vise le rendement absolu maximal en
assumant un drawdown élevé. Issue de `research/analysis_aggressive.py` +
`research/STRATEGIE_momentum_blitz.md` (nouveaux TF 15m/30m analysés).

**Edges (mesurés, nets de frais) :** ignition = breakout Donchian + surge de
volume + expansion d'ATR + alignement HTF (net-positif seulement ≥ 4h ;
15m/30m/1h perdent : frais > edge). Asymétrie MFE/MAE≈1.24, queue droite +6 %.

**Mécanique d'agression :** déploiement **plein capital** (`size_factor` 1.0→2.0
selon conviction), exits **asymétriques** (stop serré 1.3×ATR + trailing LARGE
3×ATR → laisse courir), seuil de qualité bas mais gate ignition. Long-biais
(shorts net-négatifs désactivés).

**Backtest 4h, 7.5 ans (frais/spread/borrow réalistes) :**
- full1x (réaliste) : **+58.2 %** · Sharpe **5.73** · maxDD **-11.6 %** · PF 1.55.
- lev2x (agressif) : **+113.7 %** (×2.14) · Sharpe **6.95** · maxDD -12.3 % · PF 1.74.
- Positif dans tous les régimes : BEAR 2022 flat (vs B&H -53 %), BULL +31.6 %,
  CHOP +6.4 %. Walk-forward OOS : PnL moyen +87, consistance 60 %.
- ⚠️ TF = 4h uniquement (1h/30m/15m backtestés négatifs).

> Leçon : *agressif ≠ plus de trades* (plus de frais, edge dilué). La
> sélectivité (ignition-only) maximise l'edge par trade, que le plein capital amplifie.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/strategies/momentum_blitz.py` | Stratégie agressive (`BaseStrategy`) |
| `strategies/momentum_blitz.yaml` | Params + `optimizer_results` (4h) |
| `tests/test_momentum_blitz.py` | Tests unitaires + intégration |
| `research/analysis_aggressive.py` | Analyse edges de gros mouvement nets de frais |
| `research/backtest_blitz.py` | Harnais backtest (déploiement/levier, Monte-Carlo) |
| `research/STRATEGIE_momentum_blitz.md` | Rapport analyse → conception → validation |

---

## [12.1.0] - 2026-06-03

### ✨ Nouvelle stratégie — `harmonic_regime` (confluence régime-adaptative)

Stratégie de swing **data-driven** issue d'une analyse quantitative exhaustive de
BTC 1h/4h/1d (`research/analysis_btc.py`, `research/STRATEGIE_harmonic_regime.md`).

**Edges retenus (mesurés, significatifs) :**
- LONG trend-momentum (close>EMA50>EMA200 + ADX + breakout) — t≈7-8, multi-TF.
- Clustering de volatilité (ACF|r|≈0.15-0.28) — timing d'entrée + sizing ATR.
- SHORT **défensif** en macro-bear CONFIRMÉ uniquement (propre sur 1d).
- Mean-reversion long douce en range (RSI survente). Cycle FFT + Fibonacci en
  confirmation/zones à faible poids (non significatifs comme edges autonomes).

**Posture :** longs en tendance + **FLAT en bear** (protège du DD -72 % du
Buy & Hold) + shorts opportunistes filtrés. Sizing par risque 1 %/trade, stop
ATR, trailing multi-phase (`TrailingStopManager`), max-hold.

**Backtest (7.5 ans, frais/spread/borrow réalistes) :**
- 4h : **+33.4 %**, Sharpe **5.29**, max DD **-7.3 %**, PF 1.41 ; walk-forward OOS
  consistance 60 %. BEAR 2022 : **-1.1 % vs B&H -53 %** (alpha +52 pt).
- 1d : **+11.5 %**, Sharpe **2.90**, max DD **-4.7 %**, PF 1.56 ; walk-forward OOS
  consistance **100 %**.
- ⚠️ 1h **non recommandé** : edge directionnel < coût round-trip → non rentable.

### 🔧 Fichiers ajoutés

| Fichier | Rôle |
|---------|------|
| `app/strategies/harmonic_regime.py` | Stratégie (`BaseStrategy`, score de confluence) |
| `strategies/harmonic_regime.yaml` | Params + `optimizer_results` validés (4h, 1d) |
| `tests/test_harmonic_regime.py` | Tests unitaires + intégration backtest |
| `research/analysis_btc.py` | Analyse quantitative reproductible (9 sections) |
| `research/backtest_harmonic.py` | Harnais backtest/walk-forward/split bull-bear |
| `research/STRATEGIE_harmonic_regime.md` | Rapport analyse → conception → validation |

---

## [12.0.0] - 2026-03-25

### ✨ Nouvelles fonctionnalités

#### Paper mode réaliste — slippage, capital settled, persistence

Amélioration majeure du mode simulation pour des résultats plus proches du trading réel.

**Slippage adverse :**
- Nouveau paramètre `paper_slippage` (défaut `0.001` = 0,1 %) dans `config.yaml` et l'API
- Chaque fill applique un slippage défavorable : les achats se font plus cher, les ventes moins cher
- Configurable via l'interface web (section *Paramètres de trading*)

**Suivi capital settled (`_paper_base`) :**
- Le capital settled (equity réalisée) est tracé séparément du `capital_display`
- Le PnL non réalisé des positions ouvertes est exclu du sizing du risque
- `capital_display = settled + PnL non réalisé` (synchronisé à chaque cycle paper)

**Persistence entre sessions :**
- `_restore_paper_base()` restaure le capital settled depuis la dernière `DailyStats.equity_close` en BDD
- Pas de remise à zéro du capital entre redémarrages en paper mode

**Protection capital insuffisant :**
- `_pre_execution_check()` en paper mode bloque une entrée si le capital disponible
  (`settled − notionals verrouillés`) est inférieur au notional demandé

### 🔧 Fichiers modifiés

| Fichier | Changement |
|---------|------------|
| `app/core/config.py` | `paper_slippage: 0.001` ajouté aux defaults |
| `app/live/live_trader.py` | `_paper_base`, `_restore_paper_base()`, `_sync_paper_balance()`, `_pre_execution_check()` |
| `app/live/position_mixin.py` | Slippage appliqué aux fills paper |
| `app/api/routes/config.py` | `paper_slippage` exposé dans l'API de configuration |
| `app/web/templates/config.html` | Champ *Paper slippage %* dans l'interface |

### 🗄️ Structure V12

```
app/
└── live/
    ├── live_trader.py     ← _paper_base, _restore_paper_base, _sync_paper_balance
    └── position_mixin.py  ← slippage adverse sur fills paper
```

---

## [11.0.0] - 2026-03-18

### ✨ Nouvelles fonctionnalités

#### CandleStore — Stockage Parquet persistant des bougies OHLCV

Nouveau module `app/core/candle_store.py` qui centralise tous les accès aux données OHLCV.

**Architecture :**
```
data/
└── ohlcv/
    ├── BTC_USDC/
    │   ├── 1h.parquet    (~80 KB pour 2 000 bougies)
    │   ├── 4h.parquet
    │   └── 1d.parquet
    ├── ETH_USDC/
    │   └── ...
    └── ...
```

**Principe de fetch :**
```
1er démarrage   → fetch complet depuis l'exchange (paginé si > 1 000 bougies)
                  → persistence Parquet (compression zstd)

Cycles suivants → lecture Parquet locale (< 5 ms)
                  → fetch incrémental : uniquement les nouvelles bougies
                  → merge + déduplication + persistence
```

**Couverture complète — tous les callers :**

| Module | Avant | Après |
|--------|-------|-------|
| `MarketScanner.fetch_ohlcv()` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `engine.Scanner._scan_pair()` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `engine.Scanner.get_ohlcv_df()` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `API /api/backtest` | `fetch_ohlcv_paged()` | `CandleStore.fetch()` |
| `API /api/optimize/start` | `fetch_ohlcv_paged()` | `CandleStore.fetch()` |
| `API /api/ml/train` | `fetch_ohlcv_paged()` | `CandleStore.fetch()` |
| `CLI --backtest` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| `CLI --optimize` | `exchange.fetch_ohlcv` direct | `CandleStore.fetch()` |
| LiveTrader (tous les cas) | via `scanner.fetch_ohlcv` | via `MarketScanner` → store |

**Bénéfices :**
- Indépendance exchange : backtest, optimizer, ML training utilisent le cache local
- Historique croissant automatiquement à chaque cycle live
- Aucune nouvelle dépendance (`polars` supporte Parquet nativement via PyArrow)
- Thread-safe : verrou par fichier (live trader multi-thread)
- Nouveau endpoint `GET /api/candles/stats` pour inspecter le cache

#### Découverte automatique des stratégies (`app/strategies/registry.py`)

Chaque stratégie porte ses propres métadonnées d'optimisation en attributs de classe.
L'optimiseur les découvre automatiquement — aucun fichier central à modifier
pour ajouter une nouvelle stratégie.

### 🏗️ Refactorisation (optimizer.py)

- `STRATEGY_TIMEFRAMES`, `PARAM_SPACES`, `FIXED_PARAMS` ne sont plus codés en dur
  dans `optimizer.py`. Ces dicts sont construits dynamiquement par le registre.
- Chaque `Strategy` déclare maintenant directement :
  - `timeframes`   : `List[str]` — TFs recommandés pour l'optimisation
  - `param_space`  : `Dict[str, List]` — espace de recherche des hyperparamètres
  - `fixed_params` : `Dict[str, Any]` — paramètres fixes (non optimisables)
- `BaseStrategy` expose ces attributs avec des valeurs par défaut vides.
- `RECOMMENDED_LIMIT` (config globale par TF) reste dans `optimizer.py`.
- Rétrocompatibilité totale : tous les imports existants fonctionnent.

### 🔧 Impact pour ajouter une nouvelle stratégie

**Avant (V10)** : 4 fichiers à modifier (stratégie + optimizer.py + config.yaml + doc).

**Après (V11)** : 1 seul fichier :
```python
# app/strategies/ma_nouvelle_strategie.py
class Strategy(BaseStrategy):
    name         = "ma_nouvelle_strategie"
    timeframes   = ["1h", "4h"]
    param_space  = {"period": [10, 20, 30], "rr_min": [1.3, 1.5, 2.0]}
    fixed_params = {}
    # ... min_bars_required(), score() ...
```
L'optimiseur, l'API et le live trader la détectent automatiquement.

### 🗄️ Structure V11

```
app/
└── core/
    ├── candle_store.py    ← NOUVEAU — stockage Parquet OHLCV
    ├── indicators.py
    ├── database.py
    └── exchange.py

data/
└── ohlcv/                 ← NOUVEAU — données Parquet (gitignore)
    └── {SYMBOL}/{TF}.parquet
```

---

## [10.0.0] - 2026-03-18

### ✨ Nouvelles fonctionnalités

- **Fichier indicateurs unifié** : `app/strategies/indicators.py` est **supprimé**.
  `app/core/indicators.py` est le seul et unique module d'indicateurs. Toutes les stratégies,
  le moteur et le live trader importent directement depuis `app.core.indicators`.
- **`__version__ = "10.0.0"`** dans `app/core/indicators.py` pour traçabilité programmatique.

### ⚡ Performance — Portage maximum vers Polars

Toutes les fonctions d'indicateurs sont désormais en Polars pur ; NumPy est limité à la seule
boucle séquentielle du SuperTrend (dépendance `upper[i] = f(upper[i-1])` incontournable).

| Fonction | Avant (v9) | Après (v10) |
|---|---|---|
| `_true_range` | `np.maximum` + 3 × `to_numpy()` | `pl.max_horizontal` dans DataFrame temporaire |
| `rsi` | `to_numpy()` + `np.where` + `pl.Series(arr)` | `.clip(lower_bound=1e-10)` pur Polars |
| `adx` | 6 × round-trip numpy, `np.where`, `pl.Series(arr)` | Multiplication booléenne `(up > dn).cast(Float64)` + `.clip()` |
| `supertrend` | TR/ATR calculés en numpy + boucle | TR/ATR via `_true_range()` Polars ; boucle seule en numpy |
| `precompute_df` | `np.maximum` + `pl.when(Series)` mélangé | Entièrement Polars Series + `.clip()` |

### 🐛 Corrections de bugs

- **`precompute_df`** : `pl.when(Series)` retournait un `Expr` mélangé à des `Series`, source
  d'ambiguïtés lors de l'évaluation dans `with_columns`. Remplacé par des opérations Series pures.
- **`rsi`** (standalone) : La conversion numpy masquait les `None` initiaux ; la version Polars
  les propage correctement.

### 📚 Documentation

- **`app/core/indicators.py`** : En-tête de module avec changelog détaillé des changements v10.
- **`CHANGELOG.md`** : Ce fichier — entrée v10.
- **`README.md`** : Référence mise à jour vers V10.

### 🗄️ Structure

```
app/
└── core/
    └── indicators.py    ← SOURCE UNIQUE — v10.0.0 (tous indicateurs ici)
                           app/strategies/indicators.py SUPPRIMÉ
```

### ⚡ Migration depuis V9

```python
# Ancien code (V9) — importait depuis deux modules selon le contexte :
from app.core.indicators import detect_regime, adx_val, volume_ratio
from app.strategies.indicators import rsi, atr, adx, pre_val

# Nouveau code (V10) — un seul module source :
from app.core.indicators import detect_regime, adx_val, volume_ratio, rsi, atr_val, pre_val

# app/strategies/indicators.py est supprimé — importer directement depuis app.core.indicators
# Exemple de mapping des alias courants :
#   atr_val as calc_atr     (remplace : atr as calc_atr du shim)
#   adx_val as calc_adx     (remplace : adx as calc_adx du shim)
```

---

## [9.0.0] - 2026-03-16

### ✨ Nouvelles fonctionnalités

- **Unification versioning** : V7/V8/V9 consolidée (v9.0.0)
- **Arguments CLI nettoyés** : Suppression de `--web` et `--live`
- **Caching stratégies** : TTL 300s pour `/api/backtest/settings`
- **Health check endpoint** : `GET /health` pour monitoring
- **Pagination trades** : Support offset/skip dans `/api/trades`
- **Structured logging** : Format JSON en production

### 🐛 Corrections de bugs

- **Bug #1** : Incohérence versioning (V7 vs V8 vs V9)
- **Bug #2** : Arguments CLI obsolètes `--web` et `--live` supprimés
- **Bug #3** : Argum CLI non documentés dans README (tous documentés maintenant)
- **Bug #5** : Exception silencieuse LiveTrader → maintenant logged et tracé
- **Bug #6** : Fuseau horaire non géré (UTC standardisé)
- **Bug #11** : `/api/status` sans auth → documention clarifiée

### 🔒 Sécurité

- CORS restreint en production (voir ARCHITECTURE.md)
- Validation stratégies whitelist renforcée
- API Key en header, pas en query params

### 📊 Performance

- Index DB créés : `idx_trades_symbol_strategy`, `idx_trades_time`
- Gain : -300ms sur `/api/trades`
- Cache stratégies : -40% requêtes répétées
- Polars optimisé pour backtest multiples

### 🎨 UX/UI

- Toast d'erreur API failure
- Loading spinner sur startBot/stopBot
- Modal confirmation avant actions dangereuses (exportCSV)
- Responsive design amélioré (mobile, tablette)
- Accessibilité : aria-label, lang attribute
- Dark theme supporté

### 📚 Documentation

- **README.md** : Complète, arguments CLI, OS setup, API endpoints
- **ARCHITECTURE.md** : Design patterns, threading, sécurité, performance
- **CHANGELOG.md** : Ce fichier
- **docs/SETUP.md** : Installation détaillée par OS
- **docs/API.md** : Référence API complète (TODO)
- **docs/STRATEGIES.md** : Écrire une stratégie (TODO)

### 🗄️ Structure

```
crypto_bot_v9/
├── ARCHITECTURE.md          ← NEW
├── CHANGELOG.md             ← NEW
├── CONTRIBUTING.md          ← NEW
├── docs/                    ← NEW
│   ├── SETUP.md
│   ├── API.md
│   ├── STRATEGIES.md
│   └── TROUBLESHOOTING.md
└── ... (resto inchangé)
```

### ⚡ Migration depuis V8

```bash
# 1. Remplacer la branche
git checkout main
git pull origin main

# 2. Maj config.yaml (aucun changement requis)

# 3. Redémarrer
python cli.py

# CLI anciens arguments ? Ils sont supprimés :
python cli.py --web      ❌ Erreur (avant: web-only)
python cli.py --live     ❌ Erreur (argument inexistant)

# Nouveau comportement :
python cli.py            ✅ Démarrer bot + web (live ou paper selon config)
python cli.py --paper    ✅ Forcer paper trading
```

---

## [8.0.0] - 2025-Q4

### ✨ Nouvelles fonctionnalités

- Multi-timeframe support (`/api/config/timeframes`)
- Scanner v2 avec opportunities detection
- Optimizer résultats par (strategy, timeframe)
- Server-Sent Events pour progression optimizer
- Configuration dynamique stratégies

### 🐛 Corrections

- Gestion marge trading
- Margin level warnings
- Timeout CCXT mieux géré

### 📊 Performance

- Concurrent backtest (ThreadPoolExecutor, max_workers=4)
- Validation OHLCV gaps
- Rate limiting exchanges

### 📚 Documentation

- README.md mise à jour pour V8
- Pages web améliorées

---

## [7.0.0] - 2025-Q3

### ✨ Fondations

- Architecture multi-stratégies
- Interface web (dashboard, backtest, optimizer)
- API REST FastAPI
- Backtester avec Walk-Forward et Monte-Carlo
- 5 stratégies natives (trend, pullback_trend, supertrend_macd, breakout, ml_dynamic_threshold)
- Gestion risque + circuit breaker
- Trailing stop
- Notifications (Telegram, WhatsApp)
- Optimiseur (Grid, Random, Bayesian)

### Base de données

- SQLAlchemy ORM
- Trades tracking
- Daily stats aggregation

### Exchanges

- CCXT support (Binance, Kraken, Bybit, etc.)
- Paper trading mode
- Live trading avec gestion clés API

---

## Roadmap V10+

### Prévu

- [ ] Machine Learning integration améliorée (Random Forest, LSTM)
- [ ] Backtester distribué (Celery)
- [ ] WebSocket live streaming (vs polling)
- [ ] Multi-account management
- [ ] Risk management avancé (VaR, Corr)
- [ ] Backtester GPU-accelerated (Numba)
- [ ] Mobile app (React Native)

---

## Notes importantes

### V9 est LTS (Long Term Support)

- Support 12 mois
- Backports security fixes
- Rétrocompatibilité config

### Migration V9 → V10

- Pas de breaking changes prévues
- Config YAML rétrocompatible

---

**Crypto Bot Changelog** — Suivi transparent des évolutions 📊