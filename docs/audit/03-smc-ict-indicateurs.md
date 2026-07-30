# Audit — Moteur SMC/ICT & indicateurs

> Audit post-refonte « configs par symbole » (2026-07-11). Chaque item est
> autonome : un agent peut l'exécuter avec la seule directive ci-dessous.
> Format : Priorité P1 (critique) → P3 (confort) ; Effort S/M/L.
> Discipline du repo : tout nouveau détecteur/flag est **off par défaut**
> (byte-identique vérifié par l'empreinte de régression) et doit être **mesuré**
> avec le vrai Backtester (split IS 2/3, OOS 2024+) avant activation.

### [SMC-01] SMT recalée sur la barre d'origine (sweep/impulsion), pas sur la barre de retest
- Priorité: P1 | Effort: M | Fichiers: app/strategies/smart_money.py:966-991, app/core/smc.py:954-992
- Problème/Opportunité: `smt_bonus`/`smt_filter` lisent `smt_a[i]` à la barre de RÉSOLUTION du candidat (barre de retest pour OB/BREAKER_RETEST). Le SMT ne se déclenche qu'à un nouvel extrême (~9 % des barres) alors que les retests surviennent loin des extrêmes → recouvrement quasi nul, mesuré inerte (0-1 trade modifié sur 8 ans).
- Directive: ajouter `smt_at_origin` (bool, défaut False) : pour OB_RETEST/BREAKER_RETEST, évaluer la divergence SMT à `ob["created_at"]`/`brk["created_at"]` (barre de l'impulsion d'origine) ; SWEEP_REVERSAL inchangé (déjà la barre du sweep). Précalculer le lookup dans `_build_aux`. Mesurer (BTC + ETH, 4h et 1h) : baseline vs bonus@origin vs filter@origin, rapporter nb de trades touchés et delta PF/PnL.
- Acceptation: défaut → byte-identique ; test unitaire du flag ; mesure IS/OOS rapportée.

### [SMC-02] Risque O(n²) moteur — listes `active_*` jamais bornées par âge
- Priorité: P1 | Effort: L | Fichiers: app/core/smc.py:152-157, 236-346, 376-450, 464-497 ; app/strategies/smart_money.py:380-390
- Problème/Opportunité: les boucles internes d'`analyze()` parcourent `active_pools/obs/fvgs/voids/breakers/rejections`, purgées uniquement sur sweep/touch/invalidation — jamais par âge. Une zone jamais retouchée reste indéfiniment ; `_MAX_KEEP=60` ne tronque que le dict de sortie. Sur backtest complet (~17,5k barres, ré-invoqué par combo d'optimiseur), coût par barre croissant.
- Directive: PROFILER d'abord `smc.analyze` sur BTC 4h/1h réel (taille des listes actives dans le temps, scalabilité n/2n/4n barres). Si confirmé, NE PAS changer la sémantique (`touched_at`/`invalidated_at`/`swept_at` doivent rester byte-identiques) : remplacer les scans linéaires par une structure équivalente (index trié par niveau + bisect, suppression paresseuse) fixant exactement les mêmes barres.
- Acceptation: sortie byte-identique (fixtures test_smc.py + empreinte du backtest BTC 4h) ; gain de temps mesuré et rapporté.

### [SMC-03] Liquidité calendaire PDH/PDL/PWH/PWL absente (canon SMC le plus utilisé)
- Priorité: P1 | Effort: M | Fichiers: app/core/smc.py (nouvelle primitive ~ligne 840), app/strategies/smart_money.py:132-245, 574-611
- Problème/Opportunité: le moteur ne forme des poches de liquidité QUE par clustering de swings égaux — Previous Day/Week High/Low (cible de sweep par excellence, journées UTC nettes en crypto 24/7) n'existe pas.
- Directive: ajouter `smc.calendar_liquidity_levels(df, i)` (causal, ancré 00:00 UTC) : PDH/PDL (J-1 clôturé), PWH/PWL (semaine précédente). Exposer comme `pool["kind"]` (`pdh`/`pdl`/`pwh`/`pwl`) consommable par `liquidity_targets_above/below`, flag `use_calendar_liquidity` (off). Mesurer (BTC + ETH, 4h et 1h) en deux usages : (a) cible de TP additionnelle, (b) déclencheur de SWEEP_REVERSAL.
- Acceptation: off → byte-identique ; rapport PF/PnL/Sharpe IS+OOS sur ≥2 symboles × 2 TF.

### [SMC-04] Détecteur mort : `judas_swing` jamais câblé
- Priorité: P2 | Effort: S | Fichiers: app/core/ict.py:214-237, app/strategies/smart_money.py:293-299 (pattern kz_bonus à réutiliser)
- Problème/Opportunité: `ict.judas_swing` (faux mouvement d'ouverture de session) est implémenté et testé mais n'a AUCUN appelant.
- Directive: ajouter `judas_bonus`/`judas_filter` (off) dans `_build_aux` : +0.05 au score des SWEEP_REVERSAL dont le sens correspond au signal Judas de la barre. Mesurer BTC + ETH, 4h/1h.
- Acceptation: off → byte-identique ; test de câblage ; mesure rapportée.

### [SMC-05] Détecteur mort : `std_dev_projections` jamais utilisé comme cible de TP
- Priorité: P2 | Effort: S | Fichiers: app/core/ict.py:178-190, app/strategies/smart_money.py:993-1063 (_build_trade), app/strategies/vizion.py:200-215
- Problème/Opportunité: la grille −1/−2/−2.5/−4 SD est implémentée mais jamais consommée — seules liquidité/void/volume/measured-move alimentent `targets`.
- Directive: ajouter `tp_std_dev` (off) : `ict.std_dev_projections(range_low, range_high, side)` sur le range premium/discount courant, ajouté à `targets` (même schéma que `tp_measured_move`). Mesurer BTC/ETH 4h vs baseline et vs measured_move.
- Acceptation: off → byte-identique ; mesure rapportée.

### [SMC-06] Détecteur mort : Balanced Price Range + champ `ce` jamais lu
- Priorité: P2 | Effort: M | Fichiers: app/core/ict.py:25-53, app/strategies/vizion.py:160-164
- Problème/Opportunité: `ict.balanced_price_ranges` n'a AUCUN appelant. Et là où `consequent_encroachment` sert indirectement (unicorn/propulsion dans vizion), le niveau CE (entrée 50 %) est calculé mais jamais lu — vizion entre au close, pas au CE.
- Directive: (1) câbler `balanced_price_ranges` dans smart_money comme setup optionnel `BPR_REVERSAL` (off, `use_bpr`) : zone active + tendance alignée → entrée au CE, SL de l'autre côté. (2) dans vizion, utiliser le champ `"ce"` comme prix d'entrée cible. Mesurer BTC/ETH 4h/1h.
- Acceptation: `use_bpr=False` → byte-identique smart_money ; vizion reste `enabled: false`, changement mesuré ; tests de non-régression.

### [SMC-07] Détecteur mort : `silver_bullet_flags` jamais câblé
- Priorité: P2 | Effort: S | Fichiers: app/core/ict.py:197-211, app/strategies/smart_money.py:293-299
- Problème/Opportunité: fenêtres Silver Bullet (03:00/10:00/14:00 ET, 1h) implémentées mais jamais utilisées — seules les killzones larges sont câblées.
- Directive: ajouter `sb_bonus`/`sb_filter` (off) dans `_build_aux`. Mesurer l'edge incrémental vs killzones (kz seul / kz+sb / sb seul), BTC 4h/1h/15m.
- Acceptation: off → byte-identique ; mesure rapportée.

### [SMC-08] Cohérence API : vocabulaire de cycle de vie incohérent entre familles d'entités
- Priorité: P2 | Effort: M | Fichiers: app/core/smc.py (swings `confirmed_at`/`swept_at` ~171-196 ; pools `formed_at` ~521-561 ; OB/breakers `created_at`/`touched_at`/`invalidated_at` ~280-347 ; FVG `index`/`mitigated_at`/`filled_at` ~375-406 ; voids `start_index`/`end_index` ~408-450), app/core/ict.py:129-132 (fallback défensif, symptôme)
- Problème/Opportunité: chaque famille désigne le même concept avec des clés différentes, sans schéma documenté — a déjà forcé un fallback dans `ict.propulsion_block`, complique toute primitive transverse.
- Directive: documenter le mapping {famille → clé création / clé neutralisation} en tête de smc.py SANS renommer (l'API JSON du scanner en dépend). Ajouter des accesseurs `entity_created_at(e)`/`entity_closed_at(e)` dans smc.py ; refactorer `propulsion_block` pour les utiliser.
- Acceptation: aucune clé renommée ; accesseurs testés sur chaque famille ; tests existants verts.

### [SMC-09] fast_analysis.py — écran aveugle aux signaux SMC/ICT du bot lui-même
- Priorité: P2 | Effort: M | Fichiers: app/core/fast_analysis.py:25-59
- Problème/Opportunité: le screening ne teste que 9 indicateurs classiques (familles trend/mr) — aucun signal SMC (sweep rejeté, retest OB) alors que c'est le moteur phare du bot.
- Directive: ajouter une famille `"smc"` à `build_signals` : entrées edge-triggered depuis `smc.analyze(df)` (sweeps rejetés via `_all_sweeps`, touch d'OB via `_all_obs`), TP = cible de liquidité opposée via `smc.liquidity_targets_above/below`.
- Acceptation: les 9 signaux existants inchangés ; nouveaux signaux « SMC … » dans `rows` ; test unitaire de forme.

### [SMC-10] fast_analysis.py — pas de grille de frais ni de sweep de paramètres
- Priorité: P2 | Effort: M | Fichiers: app/core/fast_analysis.py:118-140, 25-59
- Problème/Opportunité: chaque signal testé avec UN jeu de paramètres et deux niveaux de frais fixes — un edge visible seulement à une autre période/seuil de frais est manqué.
- Directive: paramètre optionnel `fee_grid` (liste de (taker,maker), défaut = actuel) + sweep léger de 2-3 variantes de période par signal, API par défaut inchangée.
- Acceptation: appel sans nouvel argument → byte-identique ; mode sweep testé unitairement.

### [SMC-11] Inducement — filtre de crédibilité présent dans vizion, absent de smart_money
- Priorité: P2 | Effort: M | Fichiers: app/strategies/vizion.py:113-122 (_recent_sweep), app/strategies/smart_money.py:844-908
- Problème/Opportunité: vizion exige un sweep opposé (inducement) près de l'origine de l'OB avant retest — smart_money OB_RETEST/BREAKER_RETEST, le setup le plus tradé, n'a PAS cette vérification.
- Directive: ajouter `require_inducement` (off) : factoriser `vizion._recent_sweep` en primitive partagée (smc.py) ; valider un retest seulement si un sweep rejeté opposé a eu lieu dans `inducement_lookback` barres avant `ob["created_at"]`. Mesurer BTC/ETH 4h (attendu : moins de trades, sélectivité accrue).
- Acceptation: off → byte-identique ; mesure rapportée (trades filtrés, delta PF).

### [SMC-12] IPDA dealing ranges — premium/discount jamais comparé à un lookback IPDA
- Priorité: P2 | Effort: M | Fichiers: app/core/smc.py:587-628 (_premium_discount_at)
- Problème/Opportunité: le canon ICT définit le dealing range via lookbacks IPDA (20/40/60) — le moteur utilise dernier swing + fenêtre fixe 100 barres, jamais comparé, alors que `zone` alimente les filtres directionnels durs.
- Directive: mode alternatif `pd_mode="ipda"` (off, défaut `"swing"` inchangé) : range = max/min glissant sur `ipda_lookback`. Mesurer smart_money BTC/ETH 4h/1d, comparer distribution des zones et PF/PnL.
- Acceptation: défaut → byte-identique ; causalité testée ; mesure rapportée.

### [SMC-13] Mitigation Block — non distingué de l'Order Block
- Priorité: P3 | Effort: M | Fichiers: app/core/smc.py:348-373
- Problème/Opportunité: un seul type de zone d'origine d'impulsion — le canon distingue le Mitigation Block (mouvement qui ne casse PAS la structure, zone plus faible) ; `strength=1` sert de proxy implicite.
- Directive: champ additif `subtype: "ob"|"mitigation"` (aucun changement par défaut) + flag `use_mitigation_blocks` (off) dans smart_money pour exclure/pondérer. Mesurer en isolant les trades sur zones mitigation vs ob.
- Acceptation: JSON existant inchangé (champ additif) ; off → byte-identique ; mesure rapportée.

### [SMC-14] PO3/AMD — compression générique, non ancrée aux sessions
- Priorité: P3 | Effort: M | Fichiers: app/strategies/smart_money.py:308-317 (amd_bonus), app/core/smc.py:844-846 (SESSIONS/KILLZONES non réutilisés)
- Problème/Opportunité: le Power of Three (Accumulation Asie → Manipulation Londres → Distribution NY) est ancré aux sessions ; l'implémentation détecte une compression générique sans vérifier session Asie + sweep à l'ouverture Londres/NY, alors que SESSIONS/KILLZONES existent.
- Directive: variante `amd_session_anchored` (off) : compression dans `SESSIONS["asia"]` (00-07 UTC) et sweep dans une KILLZONE, via `session_label`/`killzone_flags`. Mesurer BTC/ETH 4h + intraday (1h/15m) vs amd_bonus actuel.
- Acceptation: off → byte-identique ; mesure rapportée.

### [SMC-15] vizion.py — filtrage non indexé des OB HTF à chaque touch
- Priorité: P3 | Effort: S | Fichiers: app/strategies/vizion.py:106-111 (_active_htf_obs), 217-246 (_signals)
- Problème/Opportunité: `_signals` refiltre la liste COMPLÈTE `_all_obs` HTF pour chaque candidat — O(événements_LTF × OB_HTF_total). Impact nul aujourd'hui (vizion `enabled: false`) mais bloquant en multi-symboles/optimiseur.
- Directive: indexer les OB HTF par borne de validité (tri + bisect/np.searchsorted, comme `_choch_index_arrays` de smart_money) → O(log n) par appel.
- Acceptation: sortie strictement identique sur tests/test_vizion.py ; temps mesuré avant/après.
