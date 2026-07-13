# Audit — Code mort & duplication

> Audit post-refonte « configs par symbole » (2026-07-11). Chaque item est
> autonome : un agent peut l'exécuter avec la seule directive ci-dessous.
> Format : Priorité P1 (critique) → P3 (confort) ; Effort S/M/L.

### [DEAD-01] 8 générations opus_omnibus/opus_stat jamais promues (7 426 lignes mortes)
- Priorité: P1 | Effort: M | Fichiers: app/strategies/opus_omnibus_v7.py (1242 l.), opus_omnibus_v7_pretrained.py (629), opus_omnibus_v9.py (721), opus_omnibus_v10_retrained.py (1295), opus_omnibus_v11_no_ml.py (525), opus_omnibus_v11_followsetup.py (1424), opus_omnibus_v11_followsetup_no_ml.py (482), opus_stat_retrained_v4.py (1108) + leurs strategies/*.yaml
- Problème: ces 8 fichiers n'apparaissent dans AUCUNE liste active (`manual_active` config.yaml:146-161, `slot_budgets`:126-133), ni dans les routes API (scanner.py:489 ne dispatch que v8/v11/v12), ni dans les tests (grep = 0). Toutes les mentions externes sont des commentaires. 5 portent le même bug pyflakes copié-collé (`undefined name 'ds_tr'`) — figés depuis leur création (2026-05).
- ⚠ NE PAS toucher : `opus_omnibus_v8.py` (scanner.py:256 importe réellement `_DEFAULT_SETUPS`, `_classify_regime`, etc.), `opus_stat_pretrained_v4.py` (dépendance de v8, + v4_models.pkl), `opus_omnibus_v10.py` (importé par tests/test_opus_nat_safety.py:17, test_feature_store_integration.py:40,48).
- Directive: 1) supprimer les 8 .py listés ; 2) supprimer les 8 yaml correspondants ; 3) optionnel : purger les clés orphelines de data/oos_tracker.json ; 4) mettre à jour la docstring d'optimize_runner.py (~l.25) qui cite v11_no_ml ; 5) ne pas supprimer v8/v10/opus_stat_pretrained_v4.
- Acceptation: pyflakes ne référence plus ces modules ; `pytest -q` vert ; `python -c "import app.engine.registry"` OK ; grep des 8 noms sur app/ tests/ = vide.

### [DEAD-02] scoring_statistique_opus_v3 jamais promue, sans dépendant réel
- Priorité: P2 | Effort: S | Fichiers: app/strategies/scoring_statistique_opus_v3.py (579 l.), strategies/scoring_statistique_opus_v3.yaml
- Problème: seule mention externe = un commentaire dans ml.html:208. Absente de manual_active/slot_budgets et de tout test — contrairement à v4/v5 (testées) et v1/v2 (actives).
- Directive: supprimer le .py et le .yaml. Garder v1, v2 (actives), v4, v5 (testées).
- Acceptation: `pytest -q` vert ; le registre ne liste plus v3.

### [DEAD-03] data/ohlcv et data/derivatives versionnés dans git (11+ Mo)
- Priorité: P1 → **DÉCISION UTILISATEUR REQUISE** | Effort: S | Fichiers: .gitignore:21-25, data/ohlcv/**, data/derivatives/**
- Problème: .gitignore:22 a le commentaire « CandleStore — données OHLCV » mais aucune règle glob — 19 parquets OHLCV (~11 Mo) + 8 parquets derivatives versionnés, alors que ce sont des caches régénérables par `CandleStore.fetch()`.
- ⚠ NUANCE IMPORTANTE (contexte session) : le versionnement des parquets est aujourd'hui un **choix volontaire de l'utilisateur** (il les met à jour lui-même via push depuis sa machine, car l'environnement distant n'a pas accès aux exchanges). Ne pas dé-versionner sans son accord explicite. XRP_USDC est par contre absent de scanner.symbols — soit ajouter XRP/USDC aux symboles scannés (les données existent), soit supprimer le dossier.
- Directive (si accord utilisateur) : ajouter `data/ohlcv/` et `data/derivatives/` au .gitignore ; `git rm -r --cached` ; trancher le sort de XRP_USDC (ajout aux symboles OU suppression).
- Acceptation: décision documentée ; si dé-versionné : `git ls-files data/ohlcv` vide et le bot repeuple son cache sans erreur.

### [DEAD-04] _TF_MINUTES dupliqué à l'identique dans 4 fichiers
- Priorité: P2 | Effort: S | Fichiers: app/engine/backtest.py:28-31, app/engine/optimizer.py:86-89, app/api/routes/replay.py:21-24, app/core/oos_tracker.py:42-46
- Problème: le dict TF→minutes est copié verbatim dans backtest.py et optimizer.py ; replay.py et oos_tracker.py ont une variante étendue (+6h/8h/12h) — désynchronisation déjà visible.
- Directive: constante unique `TF_MINUTES` (version étendue) dans app/core/timeframes.py — **fusionner avec ARCH-08** (même nouveau module : TF_SECONDS, HTF_MAP, TF_MINUTES) ; remplacer les 4 définitions par un import.
- Acceptation: une seule définition (`grep -rn "_TF_MINUTES = {" app/` = 0) ; `pytest -q` vert.

### [DEAD-05] Bug pyflakes « undefined name » copié-collé dans 5 stratégies ML (dont une active)
- Priorité: P2 | Effort: S | Fichiers: opus_omnibus_v11.py:1093 (ACTIF, manual_active v11::30m) + 4 fichiers couverts par DEAD-01
- Problème: `del ds_tr, ds_va` dans un bloc except (v11.py:1065) rend le nom ambigu à l'itération suivante de la boucle `for target in (...)` — pyflakes `undefined name`. Motif copié-collé dans 5 fichiers.
- Directive: corriger UNIQUEMENT opus_omnibus_v11.py (bloc 1050-1094) : remplacer les deux `del` par un try/finally englobant ou une fonction interne par itération. Les 4 autres fichiers sont supprimés par DEAD-01.
- Acceptation: `pyflakes app/strategies/opus_omnibus_v11.py` sans undefined name ; `pytest -q` vert.

### [DEAD-06] 5 fonctions publiques jamais appelées (core)
- Priorité: P3 | Effort: S | Fichiers: app/core/config.py:119 (strategy_file_path), app/core/execution.py:88 (cap_notional), app/core/database.py:372 (get_lifecycle_events), app/core/feature_store.py:113,118 (get_provider, list_providers)
- Problème: grep exhaustif mot-entier sur tout le repo : zéro occurrence hors définition pour les 5.
- Directive: supprimer les 5 fonctions (ou `# noqa` + justification si usage REPL voulu). Re-vérifier par grep avant suppression.
- Acceptation: grep de chaque nom vide (hors .git) ; `pytest -q` vert.

### [DEAD-07] 67 imports inutilisés (pyflakes) hors façade indicators.py
- Priorité: P3 | Effort: M | Fichiers: ~35 fichiers (liste via la commande ci-dessous)
- Problème: `pyflakes app/ | grep -v indicators.py` → 114 diagnostics : 67 imports inutilisés, 16 variables locales, 14 f-strings sans placeholder, 16 undefined (couverts par DEAD-05). NB : app/core/indicators.py est une façade volontaire `# noqa: F401` — à exclure.
- Directive: `python -m pyflakes app/ tests/ | grep -v "app/core/indicators.py:"` puis nettoyer chaque item (ou `autoflake --remove-all-unused-imports --in-place` en excluant indicators.py). Retirer aussi le préfixe `f` des f-strings sans `{}`.
- Acceptation: la commande ci-dessus retourne 0 ligne (hors DEAD-05 tant que non traité) ; `pytest -q` vert.

### [DEAD-08] showSkeleton() jamais appelée — ⚠ CONFLIT avec UI-12
- Priorité: P3 | Effort: S | Fichiers: app/web/templates/base.html:328
- Problème: définie une fois, appelée nulle part. Deux options opposées : la supprimer (ce rapport) ou l'ADOPTER partout à la place du markup skeleton dupliqué (UI-12 du volet UI/UX).
- Directive: **trancher en faveur de UI-12** (adopter le helper — il supprime davantage de duplication) ; ne supprimer showSkeleton que si UI-12 est explicitement rejeté.
- Acceptation: cohérence avec la décision UI-12 ; pas de ReferenceError console.

### [DEAD-09] scripts/ vide — .pyc orphelin
- Priorité: P3 | Effort: S | Fichiers: scripts/__pycache__/analyze_indicators.cpython-311.pyc
- Problème: les scripts ont été supprimés (commit 0748f0f) ; seul le __pycache__ non versionné traîne sur disque.
- Directive: `rm -rf scripts/__pycache__` ; supprimer le dossier scripts/ ou y documenter l'usage prévu.
- Acceptation: `find scripts -type f` propre ; aucun impact pytest/pyflakes.
