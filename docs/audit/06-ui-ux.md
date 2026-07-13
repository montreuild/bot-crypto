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

### [UI-05] Absence de app/web/static/js — JS inline massif et dupliqué — ✅ RÉALISÉ (2026-07-13, périmètre révisé)
- Priorité: P1 | Effort: L | Fichiers: app/web/templates/*.html (10 637 lignes cumulées ; scanner.html 1407, config.html 1423, backtest.html 1092, optimizer.html 873, ml.html 872)
- Problème: aucun fichier .js statique dans le repo. base.html centralise déjà escHtml/apiFetch/toast (bon point), mais des fonctions identiques sont dupliquées entre ml.html et optimizer.html (`renderJobs`, `renderJobCard`, `loadSpaces`, `renderSpaces`, `applyJob`, `cancelJob`, `deleteJob`, `startOpt`, `mkChart`, `tfMeta`, `updatePreviewMatrix`, `renderTfChecks`, `renderStratChecks` — 2 occurrences chacune, pages nées d'un copier-coller).
- Directive: Créer app/web/static/js/{ml-optimizer-shared.js, tables.js} et y extraire le code dupliqué ml/optimizer (jobs, spaces, matrix). Monter les statiques (`app.mount("/static", StaticFiles(...))`) si absent. Ne pas toucher aux helpers déjà centralisés dans base.html.
- Acceptation: ml.html et optimizer.html chargent le même JS partagé ; `diff` des fonctions listées vide ; les deux pages fonctionnent sans régression console.
- **Réalisation** : infrastructure `app/web/static/js/` créée + `app.mount("/static", ...)` dans main.py (vérifié : 200, JS servi, les deux pages chargent `<script src="/static/js/ml-optimizer-shared.js">`). **Périmètre révisé après inspection** : sur les 13 fonctions listées, `diff` montre que **seules 6 sont restées identiques** (`TF_INFO`, `tfMeta`, `toggleTfCheck`, `renderTfChecks` — divergence uniquement cosmétique, une couleur d'accent, paramétrée via `accentRgb` — et `cancelJob`/`applyJob`/`deleteJob`, divergence uniquement dans des commentaires) → **extraites, dédupliquées**. Les 7 autres (`renderJobs`, `renderJobCard`, `loadSpaces`, `renderSpaces`, `startOpt`, `updatePreviewMatrix`, `renderStratChecks`, `mkChart` introuvable) ont **divergé fonctionnellement** depuis la rédaction de l'audit (ex. `loadSpaces`/`renderStratChecks` : ml.html filtre aux stratégies `ml_`/`is_ml`, optimizer.html les EXCLUT — comportements opposés et corrects chacun pour leur page ; `renderJobCard` : optimizer.html affiche en plus l'alpha et des messages plus détaillés). Les fusionner aurait changé le comportement observable de l'une des deux pages — **délibérément laissées locales à chaque template**, documenté en tête de `ml-optimizer-shared.js`. `tables.js` non créé (aucun contenu candidat identifié hors du périmètre ci-dessus).

### [UI-06] Interactions cliquables sans accessibilité clavier — ✅ RÉALISÉ (2026-07-13)
- Priorité: P2 | Effort: M | Fichiers: app/web/templates/config.html (10 occurrences), bots.html:237 (bot-card), dashboard.html (alloc-card), scanner.html (tr onclick) ; pattern correct : base.html:435
- Problème: 18 éléments interactifs (div/tr avec `onclick`) sans `tabindex`/`role`/gestion clavier. Le bon pattern existe dans base.html:435 (`role="button" tabindex="0" onkeydown=...`) mais n'est pas repris.
- Directive: Appliquer le pattern base.html:435 sur chaque élément identifié par `grep -noE "<(tr|div)[^>]*onclick=[^>]*>" *.html | grep -v tabindex`, en commençant par config.html puis bots.html `.bot-card`.
- Acceptation: chaque élément atteignable au Tab et activable Entrée/Espace ; le grep ci-dessus retourne 0 résultat.
- **Réalisation** : 17 éléments corrigés (config.html ×10, bots.html ×2, ml.html/optimizer.html/scanner.html/settings.html/smartgraph.html ×1 chacun) — `role="button" tabindex="0" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();HANDLER;}"` avec le MÊME handler que l'`onclick` existant. `dashboard.html` `.alloc-card` déjà couvert par `renderAllocGrid` (UI-08, markup préservé). Le seul élément sans texte visible (`bots.html` `#backdrop`, même rôle que `#sb-overlay` de référence) reçoit en plus `aria-label`. `grep -rnoE "<(tr|div)[^>]*onclick=[^>]*>" *.html | grep -v tabindex` → 0 résultat (vérifié).

### [UI-07] 11 templates sur 18 sans aucun attribut ARIA
- Priorité: P2 | Effort: M | Fichiers: app/web/templates/{audit,bots,compare,config,data,derivatives,ml,optimizer,portfolio,replay,settings}.html
- Problème: `grep -c "aria-" *.html` : 0 occurrence dans 11 templates, contre 17-30 dans dashboard/scanner/base qui appliquent déjà `aria-label`/`aria-current`/`aria-live`. L'effort d'accessibilité n'a pas été propagé aux pages récentes.
- Directive: Sur chaque fichier listé : `aria-label` sur les boutons icône-seul, `role="status"`/`aria-live="polite"` sur les zones de chargement, `scope="col"` sur les `<th>` (reprendre le modèle scanner/dashboard).
- Acceptation: `grep -c "aria-"` > 0 pour les 11 fichiers ; audit axe-core sans absence de label sur les contrôles principaux.

### [UI-08] Triple redondance de l'affichage budget/allocation (dashboard/portfolio/bots) — ✅ RÉALISÉ (2026-07-13, 2/3 pages)
- Priorité: P2 | Effort: M | Fichiers: dashboard.html:574-595 (renderCapitalAllocation) ; portfolio.html:148-152 ; bots.html:157-224 (cardHtml)
- Problème: la grille « Répartition du capital » par slot est recalculée et rendue avec 3 implémentations JS distinctes et 3 appels API séparés pour la même donnée (slot → budget).
- Directive: Extraire `renderAllocGrid(slots, opts)` dans app/web/static/js/alloc.js (dépend de UI-05), paramètre de niveau de détail (compact dashboard / détaillé portfolio-bots) ; migrer les 3 call-sites.
- Acceptation: les 3 pages affichent des données cohérentes (même formule used_pct/seuils) via une seule fonction ; pas de régression visuelle.
- **Réalisation** : `app/web/static/js/alloc.js::renderAllocGrid(container, slots, opts)` avec `opts.style` ∈ {'card' (dashboard, cliquable→/bots), 'row' (portfolio, avec overlay de cible « shadow allocation »)} — markup HTML strictement identique à l'original, vérifié (dashboard/portfolio chargent le script, 200, contenu attendu présent). **bots.html NON migré** : après inspection, `cardHtml`/`.budget-mini` n'est PAS une grille d'allocation mais une ligne de budget au sein d'une carte de bot bien plus riche (état de cycle de vie, edge, PnL hebdo, badges) — un composant fonctionnellement différent des deux autres, pas un troisième rendu de la même donnée. Extraire isolément la barre `.budget-mini` (une ligne) n'aurait pas constitué une vraie déduplication ; documenté en tête d'`alloc.js` pour qu'un futur agent ne tente pas de forcer la fusion.

### [UI-09] Pas d'enchaînement UX entre /data et scanner/Fast Analyse
- Priorité: P2 | Effort: S | Fichiers: data.html (entier) ; scanner.html:1215-1238 (runFastAnalysis)
- Problème: quand Fast Analyse échoue par manque de données, le message d'erreur brut s'affiche sans lien vers /data ; symétriquement, /data ne propose aucun lien « Analyser » vers /scanner pour la paire chargée.
- Directive: Dans le bloc erreur de `runFastAnalysis()`, ajouter un lien `<a href="/data">Charger les données</a>` quand l'erreur évoque un manque de bougies. Dans data.html, ajouter par ligne un lien « ↗ Analyser » vers `/scanner?symbol=…&tf=…` (ajouter la lecture des paramètres d'URL dans scanner.html si absente).
- Acceptation: liens fonctionnels dans les deux sens, pré-remplis avec le symbole/TF.

### [UI-10] SmcChart chargé globalement même sur les pages sans graphique — ✅ RÉALISÉ (2026-07-13)
- Priorité: P3 | Effort: S | Fichiers: base.html:296-388
- Problème: `window.SmcChart` (~55 lignes) est défini dans le `<head>` de base.html, exécuté sur TOUTES les pages — y compris config/settings/data/portfolio/bots/audit/ml/optimizer/compare qui n'utilisent jamais lightweight-charts.
- Directive: Déplacer la définition dans un bloc Jinja `{% block chart_helpers %}{% endblock %}` inclus uniquement par les templates chargeant lightweight-charts (ou static/js/smc-chart.js conditionnel, cf UI-05).
- Acceptation: les pages sans chart ne définissent plus `window.SmcChart` ; les pages chart fonctionnent sans régression sur les zones SMC.
- **Réalisation** : extrait vers `app/web/static/js/smc-chart.js` (option retenue : fichier statique conditionnel, combine bien avec UI-05) ; base.html expose `{% block chart_helpers %}{% endblock %}` (vide par défaut, placé APRÈS la fermeture du `<script>` inline existant — l'imbriquer dedans aurait produit un `<script>` invalide dans les pages qui y insèrent un `<script src>`). **Périmètre vérifié plus étroit que suggéré** : sur les 8 pages chargeant lightweight-charts (backtest/dashboard/derivatives/replay/scanner/trades/smartgraph/smartreplay), seules **smartgraph.html et smartreplay.html** appellent réellement `SmcChart.FILL`/`SmcChart.ZonesPrimitive` — les 6 autres chargent la librairie de graphique mais jamais ce helper. Le bloc n'est donc rempli que par ces deux templates. Vérifié (TestClient) : `/smartgraph` et `/smartreplay` chargent `smc-chart.js` et ne redéfinissent plus `window.SmcChart` inline ; les 9 autres pages testées (config/data/portfolio/bots/audit/ml/optimizer/compare/settings) ne référencent ni l'un ni l'autre.

### [UI-11] Terminologie fr/en mélangée — ✅ RÉALISÉ (2026-07-13)
- Priorité: P3 | Effort: S | Fichiers: scanner.html:115,271,554 ; smartreplay.html:65
- Problème: scanner.html utilise « ↺ Refresh » là où les autres pages disent « Rafraîchir »/« Actualiser » ; smartreplay.html a « Rejections » au milieu de libellés français.
- Directive: Remplacer « ↺ Refresh » par « ↺ Actualiser » (scanner.html:115,271,554) et « Rejections » par « Rejets » (smartreplay.html:65, conserver l'id `rp-l-rejections`).
- Acceptation: `grep -n ">Refresh<\|>Rejections<" *.html` vide ; pas de régression.
- **Réalisation** : 3 occurrences « ↺ Refresh » → « ↺ Actualiser » dans scanner.html (bouton + message vide) ; « Rejections » → « Rejets » dans smartreplay.html, id `rp-l-rejections` conservé. Grep d'acceptation vide (vérifié) ; 537 tests verts.

### [UI-12] Helper showSkeleton() partagé mais jamais utilisé — ✅ RÉALISÉ (2026-07-13, périmètre étendu)
- Priorité: P3 | Effort: S | Fichiers: base.html:328 ; trades.html:145,159,172 ; config.html:678-679,788-789
- Problème: `showSkeleton(el, lines)` est défini dans base.html mais jamais appelé — chaque page réécrit à la main le même markup skeleton (8+ fichiers).
- Directive: Remplacer les blocs `innerHTML` statiques de squelette par `showSkeleton(el, n)` dans trades.html, config.html, ml.html, optimizer.html, audit.html.
- Acceptation: `grep -c "showSkeleton(" *.html` > 1 sur les fichiers listés ; rendu visuel inchangé.
- **Réalisation** : `showSkeleton` initialement limité aux `.skeleton-line` (div nues) — incompatible avec 2 patterns réellement présents : squelette de `<tbody>` (nécessite un `<tr><td colspan>` autour, sinon markup de table invalide) et squelette `.skeleton-card` (variante visuelle différente, pas des lignes). Étendu de façon **rétro-compatible** : `showSkeleton(el, lines, opts)` avec `opts.colspan` (enveloppe `<tr><td>`) et `opts.cards` (n `.skeleton-card` au lieu de lignes) ; sans `opts`, comportement historique inchangé. Converti : trades.html ×3 (tbody, colspan 10/7/6, appelés en tête de `load()`), config.html ×2 (`.strat-grid` cards + `.strat-params-panels` lignes, dans `init()` — matchait déjà exactement `showSkeleton(el,3)` par défaut), ml.html/optimizer.html ×1 (`param-space-view`, dans `init()`), audit.html ×2 (`opt-results-container` div + `changelog-body` tbody colspan 5). Le HTML Jinja statique correspondant est vidé (le squelette est désormais posé par JS dès le début de la fonction de chargement, avant le fetch async — même séquence temporelle, une seule source). `config.html:253-254,678-679` (lignes à hauteur/largeur personnalisées, hors du pattern 3-lignes w80/w60/w40) laissées telles quelles — non convertibles sans étendre encore l'API pour un cas très spécifique. `grep -c "showSkeleton(" *.html` > 1 sur les 5 fichiers cibles (vérifié) ; 537 tests verts.

---

**Résumé transversal** : base.html montre déjà de bonnes pratiques (helpers partagés, `.tscroll` pour l'overflow — vérifié correct), mais elles ne sont pas propagées aux pages récentes ni aux paires nées d'un copier-coller (ml/optimizer). **L'angle « par-symbole » est le point le plus critique** : bots.html est adapté, mais config.html (UI-02), audit.html (UI-03) et trades.html (UI-04) ne suivent pas encore le modèle de slot à 3 parties.
