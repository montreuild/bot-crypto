# Audit — Interface Web (UI/UX)

> Audit post-refonte « configs par symbole » (2026-07-11). Chaque item est
> autonome : un agent peut l'exécuter avec la seule directive ci-dessous.
> Format : Priorité P1 (critique) → P3 (confort) ; Effort S/M/L.

### [UI-01] Échappement HTML incohérent et faille XSS dans data.html
- Priorité: P1 | Effort: S | Fichiers: app/web/templates/data.html:61,73,102 ; app/web/templates/backtest.html:964 ; app/web/templates/base.html:297
- Problème: `base.html:297` définit `escHtml()` (échappe `& < > " '`) partagé par toutes les pages. Mais `data.html:61` redéfinit sa propre fonction `esc()` qui n'échappe que `& < >` (pas les guillemets). Elle est utilisée en contexte attribut à `data.html:73` : `onclick="fetchOne(\''+esc(x.symbol)+'\',...)"`. Un symbole contenant un guillemet simple (atteignable via le formulaire « Fetch manuel », champ `mf-sym`, puis renvoyé par `/api/data/status`) casse l'attribut et permet l'injection de JS. `backtest.html:964` a une 3e implémentation `escHtmlBt()`, non synchronisée.
- Directive: Supprimer `esc()` (data.html:61) et `escHtmlBt()` (backtest.html:964) ; remplacer tous leurs appels par `escHtml()` partagé de base.html. Dans data.html:73, remplacer les `onclick` inline par `data-symbol`/`data-tf` + `addEventListener` (élimine le risque de casse d'attribut). Vérifier `grep -n "esc(" data.html` après suppression.
- Acceptation: `grep -rn "function esc(\|function escHtmlBt(" app/web/templates/*.html` vide ; /data se charge sans erreur console ; un symbole contenant `'` dans « Fetch manuel » n'exécute aucun script.

### [UI-02] config.html reste mono-symbole malgré le moteur per-symbole
- Priorité: P1 | Effort: L | Fichiers: app/web/templates/config.html (1423 lignes, 0 occurrence de « symbol ») ; app/api/routes/config.py:283-355
- Problème: `grep -n "symbol" config.html` ne renvoie rien : l'édition des paramètres de stratégie et l'activation par timeframe sont globales. Côté backend, `update_strategy_params(strategy, params)` et `toggle_strategy_timeframe(...)` n'acceptent aucun `symbol`. Or le moteur construit désormais des slots `strategy::tf::symbol`. Impossible de configurer BTC/USDC vs ETH/USDC différemment depuis l'UI.
- Directive: Ajouter un sélecteur de symbole (optionnel, « Tous » par défaut) dans la section « Paramètres de stratégie » ; étendre `/api/config/strategy-params` et `/api/config/strategy-timeframe` pour accepter un `symbol` optionnel et écrire dans `optimizer_results[tf][symbol]` (réutiliser `apply_best_params(symbol=…)` / `_select_symbol_entry` — NE PAS inventer un second schéma `per_symbol` divergent) ; afficher les overrides actifs par symbole à côté du réglage global.
- Acceptation: config.html permet de sauvegarder un paramètre pour un symbole précis sans modifier les autres ; test : modifier un param pour BTC/USDC, vérifier qu'ETH/USDC garde l'ancienne valeur ; aucune régression console.

### [UI-03] audit.html écrase les résultats OOS entre symboles
- Priorité: P1 | Effort: M | Fichiers: app/web/templates/audit.html:66-75,97-99 ; app/core/backtest_history.py:57 ; app/core/oos_tracker.py:279
- Problème: `loadOptResults()` reconstruit `byStratTf[entry.strategy][entry.tf] = {...}` en ignorant `entry.symbol` — avec plusieurs symboles pour le même strategy+tf (cas normal désormais), seule la dernière entrée reste visible. `backtest_history.py:57` construit aussi `slot_key = f"{strategy}::{timeframe}"` (2 parties) : l'historique de backtest d'un symbole écrase celui d'un autre côté backend.
- Directive: Indexer `byStratTf[strategy][tf]` par sous-clé `entry.symbol||'*'` ; adapter `renderOptResults`/`btLine` pour afficher le meilleur résultat par symbole ou la liste des symboles avec score. Backend : passer `backtest_history.py:57` au slot 3-parties via `bot_identity._slot_key(strategy, tf, symbol)`.
- Acceptation: avec 2 symboles ayant des résultats OOS sur le même strategy+tf, les deux apparaissent dans « TOP par Timeframe » ; aucune régression JS console sur /audit.

### [UI-04] trades.html : filtre « Slot » incompatible avec le modèle per-symbole
- Priorité: P1 | Effort: S | Fichiers: app/web/templates/trades.html:368-377
- Problème: `buildSlotFilter()` construit la clé `t.strategy+'::'+t.timeframe` (2 parties). Partout ailleurs (bots, portfolio, allocateur) « slot » = 3 parties avec symbole. Le filtre regroupe donc plusieurs bots distincts sous une option, en confusion avec le filtre « Paire » voisin.
- Directive: Reconstruire la clé avec le symbole (`t.strategy+'::'+t.timeframe+'::'+t.symbol`) et adapter le split (3 éléments) pour aligner sur bots.html/portfolio.html ; libellé = `Stratégie TF · Paire`.
- Acceptation: filtrer sur un slot ne mélange plus deux symboles ; pas de régression sur le filtrage existant.

### [UI-05] Absence de app/web/static/js — JS inline massif et dupliqué
- Priorité: P1 | Effort: L | Fichiers: app/web/templates/*.html (10 637 lignes cumulées ; scanner.html 1407, config.html 1423, backtest.html 1092, optimizer.html 873, ml.html 872)
- Problème: aucun fichier .js statique dans le repo. base.html centralise déjà escHtml/apiFetch/toast (bon point), mais des fonctions identiques sont dupliquées entre ml.html et optimizer.html (`renderJobs`, `renderJobCard`, `loadSpaces`, `renderSpaces`, `applyJob`, `cancelJob`, `deleteJob`, `startOpt`, `mkChart`, `tfMeta`, `updatePreviewMatrix`, `renderTfChecks`, `renderStratChecks` — 2 occurrences chacune, pages nées d'un copier-coller).
- Directive: Créer app/web/static/js/{ml-optimizer-shared.js, tables.js} et y extraire le code dupliqué ml/optimizer (jobs, spaces, matrix). Monter les statiques (`app.mount("/static", StaticFiles(...))`) si absent. Ne pas toucher aux helpers déjà centralisés dans base.html.
- Acceptation: ml.html et optimizer.html chargent le même JS partagé ; `diff` des fonctions listées vide ; les deux pages fonctionnent sans régression console.

### [UI-06] Interactions cliquables sans accessibilité clavier
- Priorité: P2 | Effort: M | Fichiers: app/web/templates/config.html (10 occurrences), bots.html:237 (bot-card), dashboard.html (alloc-card), scanner.html (tr onclick) ; pattern correct : base.html:435
- Problème: 18 éléments interactifs (div/tr avec `onclick`) sans `tabindex`/`role`/gestion clavier. Le bon pattern existe dans base.html:435 (`role="button" tabindex="0" onkeydown=...`) mais n'est pas repris.
- Directive: Appliquer le pattern base.html:435 sur chaque élément identifié par `grep -noE "<(tr|div)[^>]*onclick=[^>]*>" *.html | grep -v tabindex`, en commençant par config.html puis bots.html `.bot-card`.
- Acceptation: chaque élément atteignable au Tab et activable Entrée/Espace ; le grep ci-dessus retourne 0 résultat.

### [UI-07] 11 templates sur 18 sans aucun attribut ARIA
- Priorité: P2 | Effort: M | Fichiers: app/web/templates/{audit,bots,compare,config,data,derivatives,ml,optimizer,portfolio,replay,settings}.html
- Problème: `grep -c "aria-" *.html` : 0 occurrence dans 11 templates, contre 17-30 dans dashboard/scanner/base qui appliquent déjà `aria-label`/`aria-current`/`aria-live`. L'effort d'accessibilité n'a pas été propagé aux pages récentes.
- Directive: Sur chaque fichier listé : `aria-label` sur les boutons icône-seul, `role="status"`/`aria-live="polite"` sur les zones de chargement, `scope="col"` sur les `<th>` (reprendre le modèle scanner/dashboard).
- Acceptation: `grep -c "aria-"` > 0 pour les 11 fichiers ; audit axe-core sans absence de label sur les contrôles principaux.

### [UI-08] Triple redondance de l'affichage budget/allocation (dashboard/portfolio/bots)
- Priorité: P2 | Effort: M | Fichiers: dashboard.html:574-595 (renderCapitalAllocation) ; portfolio.html:148-152 ; bots.html:157-224 (cardHtml)
- Problème: la grille « Répartition du capital » par slot est recalculée et rendue avec 3 implémentations JS distinctes et 3 appels API séparés pour la même donnée (slot → budget).
- Directive: Extraire `renderAllocGrid(slots, opts)` dans app/web/static/js/alloc.js (dépend de UI-05), paramètre de niveau de détail (compact dashboard / détaillé portfolio-bots) ; migrer les 3 call-sites.
- Acceptation: les 3 pages affichent des données cohérentes (même formule used_pct/seuils) via une seule fonction ; pas de régression visuelle.

### [UI-09] Pas d'enchaînement UX entre /data et scanner/Fast Analyse
- Priorité: P2 | Effort: S | Fichiers: data.html (entier) ; scanner.html:1215-1238 (runFastAnalysis)
- Problème: quand Fast Analyse échoue par manque de données, le message d'erreur brut s'affiche sans lien vers /data ; symétriquement, /data ne propose aucun lien « Analyser » vers /scanner pour la paire chargée.
- Directive: Dans le bloc erreur de `runFastAnalysis()`, ajouter un lien `<a href="/data">Charger les données</a>` quand l'erreur évoque un manque de bougies. Dans data.html, ajouter par ligne un lien « ↗ Analyser » vers `/scanner?symbol=…&tf=…` (ajouter la lecture des paramètres d'URL dans scanner.html si absente).
- Acceptation: liens fonctionnels dans les deux sens, pré-remplis avec le symbole/TF.

### [UI-10] SmcChart chargé globalement même sur les pages sans graphique
- Priorité: P3 | Effort: S | Fichiers: base.html:296-388
- Problème: `window.SmcChart` (~55 lignes) est défini dans le `<head>` de base.html, exécuté sur TOUTES les pages — y compris config/settings/data/portfolio/bots/audit/ml/optimizer/compare qui n'utilisent jamais lightweight-charts.
- Directive: Déplacer la définition dans un bloc Jinja `{% block chart_helpers %}{% endblock %}` inclus uniquement par les templates chargeant lightweight-charts (ou static/js/smc-chart.js conditionnel, cf UI-05).
- Acceptation: les pages sans chart ne définissent plus `window.SmcChart` ; les pages chart fonctionnent sans régression sur les zones SMC.

### [UI-11] Terminologie fr/en mélangée
- Priorité: P3 | Effort: S | Fichiers: scanner.html:115,271,554 ; smartreplay.html:65
- Problème: scanner.html utilise « ↺ Refresh » là où les autres pages disent « Rafraîchir »/« Actualiser » ; smartreplay.html a « Rejections » au milieu de libellés français.
- Directive: Remplacer « ↺ Refresh » par « ↺ Actualiser » (scanner.html:115,271,554) et « Rejections » par « Rejets » (smartreplay.html:65, conserver l'id `rp-l-rejections`).
- Acceptation: `grep -n ">Refresh<\|>Rejections<" *.html` vide ; pas de régression.

### [UI-12] Helper showSkeleton() partagé mais jamais utilisé
- Priorité: P3 | Effort: S | Fichiers: base.html:328 ; trades.html:145,159,172 ; config.html:678-679,788-789
- Problème: `showSkeleton(el, lines)` est défini dans base.html mais jamais appelé — chaque page réécrit à la main le même markup skeleton (8+ fichiers).
- Directive: Remplacer les blocs `innerHTML` statiques de squelette par `showSkeleton(el, n)` dans trades.html, config.html, ml.html, optimizer.html, audit.html.
- Acceptation: `grep -c "showSkeleton(" *.html` > 1 sur les fichiers listés ; rendu visuel inchangé.

---

**Résumé transversal** : base.html montre déjà de bonnes pratiques (helpers partagés, `.tscroll` pour l'overflow — vérifié correct), mais elles ne sont pas propagées aux pages récentes ni aux paires nées d'un copier-coller (ml/optimizer). **L'angle « par-symbole » est le point le plus critique** : bots.html est adapté, mais config.html (UI-02), audit.html (UI-03) et trades.html (UI-04) ne suivent pas encore le modèle de slot à 3 parties.
