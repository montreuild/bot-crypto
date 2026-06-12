# Vision cible — Portefeuille de bots autonomes

> Réponse aux questions : « pourquoi l'OOS plutôt que le backtest comme déterminant
> d'activation ? », « peut-on imaginer chaque slot comme un bot indépendant avec son budget ? »,
> et proposition de la vision globale idéale + organisation des pages UI.
>
> Complète `ANALYSE_CYCLE_DE_VIE_STRATEGIES.md` (état des lieux) — ce document décrit la cible.

---

## 1. Le déterminant d'activation idéal : ni l'OOS figé, ni le backtest complet

### 1.1 Pourquoi pas le backtest complet ?

L'OOS **est** un backtest — exécuté sur des données que l'optimiseur n'a pas vues. Le backtest
complet inclut la partie in-sample, sur laquelle les paramètres ont été choisis précisément parce
qu'ils y performent : ce score mesure la qualité de l'ajustement, pas celle de la stratégie.
L'utiliser comme déterminant d'activation sélectionnerait systématiquement les stratégies les plus
overfittées.

### 1.2 Le vrai défaut de l'existant : l'OOS est une photo figée

Le score OOS est calculé une fois, à l'optimisation, puis reste gravé dans `optimizer_results`
pendant des semaines. Le marché bouge, le score non. La sélection top-N par timeframe pilote donc
l'activation avec une information périmée.

### 1.3 La solution : le forward-test glissant

> **Re-backtester chaque jour les paramètres figés sur les données les plus récentes — des données
> qui n'existaient pas au moment de l'optimisation.**

C'est mathématiquement un OOS en expansion permanente :

- **Honnête** : données postérieures au choix des paramètres, zéro fuite possible.
- **Riche statistiquement** : un slot fait parfois 2 trades/semaine en live ; le backtest glissant
  sur 30–60 jours produit beaucoup plus d'observations. On n'attend pas 2 mois de live pour
  détecter une stratégie morte.
- **Frais** : détecte un changement de régime en quelques jours.
- **Bon marché** : le moteur de backtest existe, les données OHLCV sont publiques, le coût CPU
  d'un backtest quotidien par slot actif est négligeable.

### 1.4 Le score d'activation composite à trois horizons

| Horizon | Source | Rôle | Poids indicatif |
|---|---|---|---|
| Naissance | OOS d'optimisation + walk-forward | Ticket d'entrée (le droit d'exister) | Gate binaire |
| Continu | **Forward-test glissant** (params figés, 30–60 j) | Moteur principal du budget | ~60 % |
| Confirmation | Trades réels vs fourchette Monte-Carlo | Le réel confirme-t-il la simulation ? (slippage, exécution) | ~40 %, monte avec le nb de trades réels |

### 1.5 L'activation binaire est une mauvaise abstraction

La décision idéale n'est pas ON/OFF avec des seuils magiques, c'est un **budget continu** :

- score qui monte → budget qui monte (borné, lissé) ;
- score qui baisse → budget qui fond progressivement ;
- budget sous un plancher (minimum de notional exchange) → slot de fait désactivé →
  re-optimisation planifiée automatiquement.

Avantages : pas de flapping (le lissage fait office d'hystérésis naturelle), pas de seuil
d'activation à débattre, dégradation douce au lieu de coupures brutales. Les « états » du cycle de
vie ne subsistent que comme **lecture humaine** de la trajectoire du budget.

---

## 2. Chaque slot = un bot indépendant avec son budget

### 2.1 Le principe de fidélité au backtest

L'intuition « si un trade n'est pas ouvert, c'est potentiellement celui qui rattrape les pertes »
est exacte et porte un nom : **fidélité au backtest**. Le backtest qui a validé la stratégie a pris
*tous* ses trades, y compris après une série de pertes — la distribution validée inclut les trades
de rattrapage. Chaque veto live (max_positions global, pause après 3 pertes consécutives,
win-rate floor, taille ×0.5 du volatility brake) crée une stratégie *différente* de celle validée :

- ses statistiques live ne sont plus comparables à son OOS ;
- on l'ampute de trades qui font partie de sa distribution de gains ;
- les circuit breakers actuels (pause 30 min après 3 pertes) coupent précisément dans les
  drawdowns que le backtest avait déjà traversés et validés.

**Conclusion : les protections trade par trade au niveau global sont contre-productives. La bonne
unité de protection est le budget du bot.**

### 2.2 Architecture cible : le fonds et ses traders

**À l'intérieur d'un bot (slot) — souveraineté totale :**

| Aspect | Règle |
|---|---|
| Capital | Portefeuille virtuel propre = son budget. Courbe d'équité propre, DD propre. |
| Sizing | Exactement la formule du backtest : `risque % × budget_du_bot / distance_de_stop`. La divergence live/backtest disparaît par construction. |
| Trades | Prend **tous** ses signaux tant qu'il a du budget. Aucun veto externe trade par trade. |
| Stop ultime | Épuiser son budget. Perte maximale = budget confié, bornée par conception. |
| Paramètres | **Figés à la naissance.** Un bot = (stratégie, timeframe, params, version). |

**Au niveau du gestionnaire (méta-couche) — il alloue, il ne trade pas :**

| Aspect | Règle |
|---|---|
| Allocation | Budgets ∝ score composite (§1.4), rebalance quotidien, variation bornée (±25 %). |
| Corrélation | Deux bots dont les PnL quotidiens sont corrélés > 0.7 se partagent une enveloppe commune. Remplace les vetos long/short actuels. |
| Réserve | ~10 % du capital jamais alloué (frais, slippage, minimums d'ordre). |
| Contraintes exchange | Minimum de notional vérifié à l'allocation : un bot dont le budget ne permet pas un ordre valide n'est pas activé. |
| Kill-switch | **Unique** protection globale : équité totale −X % (ex. 20 %) → tout coupe. Seul cas de veto global. |
| Ex-circuit-breakers | Pertes consécutives, win-rate bas, etc. ne sont plus des pauses : ce sont des **entrées du score d'allocation** (budget réduit au prochain rebalance) — et seulement si la performance sort de la fourchette Monte-Carlo validée, pas sur une série de pertes déjà présente dans le backtest. |

**Compromis assumé** : l'exposition simultanée peut être plus élevée qu'avec `max_positions: 5`
quand plusieurs bots signalent en même temps. Contenu par : Σ budgets ≤ 90 %, malus de corrélation,
kill-switch. C'est le bon endroit pour gérer ce risque — au niveau du portefeuille, pas en mutilant
les stratégies. Point d'attention d'implémentation : en spot, le cash est physiquement partagé →
comptabilité de trésorerie par bot nécessaire (réservation du notional à l'entrée, comme
`used_notional` aujourd'hui, mais par portefeuille virtuel).

### 2.3 Versioning des bots

On ne modifie **jamais** les paramètres d'un bot vivant — cela invaliderait son historique. Une
re-optimisation crée un **v2** :

- v2 naît en Essai (paper) pendant que v1 continue avec son budget ;
- A/B test naturel entre générations ; v1 décline par son score, v2 monte par le sien ;
- chaque track record reste attaché aux paramètres exacts qui l'ont produit ;
- identité d'un bot = `(stratégie, timeframe, hash_des_params)` → l'historique en DB devient
  non ambigu.

---

## 3. Le cycle de vie cible

```
Laboratoire ──► 🥚 Candidat ──► 🔵 Essai (paper, budget virtuel) ──► 🟢 Actif (budget réel,
 (optimisation,    OOS + WF OK        forward-test glissant +              modulé en continu)
  params figés)                       fourchette MC respectée                    │
                                                                    score qui fond │ budget → 0
        ▲                                                                        ▼
        └──── re-optimisation auto = naissance d'un v2 ◄──────────────── ⚪ Retiré (v1 archivé
                                                                            avec son historique)
```

- **Candidat → Essai** : OOS > 0 avec ≥ 10 trades, overfit ≤ 2.0, walk-forward positif sur la
  majorité des folds. À la création, on fige le *contrat de performance* : fourchette Monte-Carlo
  du PnL attendu (le `MonteCarloSimulator` existe déjà et est inutilisé).
- **Essai → Actif** : N trades paper ou N jours, PnL dans la fourchette, forward-test glissant
  positif.
- **Actif** : budget recalculé quotidiennement par le score composite. Pas de seuil de sortie
  explicite — le budget fond si le score fond.
- **Retiré** : budget sous le plancher → archivage du track record, re-optimisation planifiée → v2.
- **Override manuel** : Geler (sortir du pilotage auto), Booster, Retirer — toujours prioritaire,
  toujours affiché.

Chaque transition : datée, motivée en une phrase, notifiée (Telegram + fil d'activité UI).

---

## 4. Organisation et contenu des pages UI

**Métaphore porteuse : l'utilisateur est le gérant d'un fonds qui emploie des bots-traders.**
Il décide du capital, du profil de risque, et garde un droit de veto par bot. Il n'édite jamais de
YAML, ne choisit jamais un seuil technique. Le système explique chaque décision en une phrase.

### ① Portefeuille (accueil — remplace Dashboard)

- **Bandeau de santé** en français : « Tout va bien — 6 bots actifs, +3,2 % ce mois » /
  « ⚠ Kill-switch armé : −18 % sur −20 % autorisés ».
- Équité totale (courbe) avec **contribution par bot** (aires empilées ou sélection).
- Donut d'allocation : budget par bot + réserve.
- Jauge de risque du jour, positions ouvertes regroupées **par bot**.
- **Fil d'activité** = miroir exact de Telegram : « 12/06 14:30 — breakout·1h v2 promu Actif,
  budget 12 % » / « 11/06 — fear_momentum·4h : budget réduit 8 %→5 % (sous la fourchette
  attendue sur 14 trades) ».
- Bouton unique d'arrêt d'urgence (kill-switch manuel) avec confirmation.

### ② Mes Bots (fusionne Config-stratégies et Slots)

- **Kanban par état** : Candidats | Essai | Actifs | Retirés (et « Gelés » en filtre).
- **Carte bot** : nom + TF + version (`breakout · 1h · v2`), sparkline équité 30 j, budget avec
  flèche de tendance, et **l'indicateur de confiance** : 🟢 conforme à la fourchette attendue /
  🟠 limite / 🔴 décroche.
- **Fiche bot** (clic) :
  - LA visualisation clé : courbe d'équité réelle superposée au **cône Monte-Carlo** du contrat de
    performance — lecture immédiate de « est-ce que le réel confirme la simulation ? » ;
  - forward-test glissant (le « backtest d'hier ») vs réel ;
  - liste des trades, stats du bot (sur SON budget), journal de vie complet ;
  - paramètres en **lecture seule** + bouton « Re-optimiser → créer v2 » ;
  - actions : Geler / Booster / Retirer.
- Tri par budget, confiance, PnL. Aucun slider de budget en mode auto (lecture seule) ; sliders
  visibles uniquement en mode manuel/gelé.

### ③ Laboratoire (fusionne Backtest et Optimiseur)

- Orienté **pipeline et verdict**, pas options techniques :
  1. choisir stratégie + TF + paire ;
  2. « Analyser » : optimisation avec walk-forward et Monte-Carlo **systématiques** (plus des
     cases à cocher) ;
  3. **Verdict en clair** : « ✅ Candidat viable — 14 trades OOS, profitable sur 4/5 fenêtres,
     fourchette attendue : +2 % à +11 % sur 30 j » ou « ❌ Rejeté — résultat 3× meilleur sur
     l'entraînement que sur le test (overfit). Conseil : plus de bougies ou moins de paramètres » ;
  4. bouton unique : **« Créer le bot (Essai) »**. Le labo produit des candidats, jamais des
     écritures YAML directes.
- Mode expert (opt-in) : accès aux trials, top-5, espaces de paramètres, exports — l'existant.
- Le backtest manuel libre reste disponible comme outil d'exploration dans un onglet « Bac à sable ».

### ④ Marché (Scanner actuel)

- Inchangé sur le fond ; ajout d'un raccourci « Analyser cette paire au Laboratoire ».

### ⑤ Réglages

- Capital, **profil de risque en 3 presets** (Prudent / Équilibré / Agressif → kill-switch,
  réserve, risque par trade des bots, agressivité d'allocation), paper/live, clés exchange,
  notifications.
- Mode expert (opt-in) : tous les paramètres fins actuels.

### Transversal

- Tooltip pédagogique systématique sur chaque terme (OOS, Sharpe, fourchette, overfit) —
  composant unique réutilisé.
- Toast de confirmation après chaque action, distinguant « appliqué au bot en cours » vs
  « enregistré pour le prochain démarrage ».
- Confirmation modale avant : retirer un bot avec position ouverte, kill-switch, passage en live.

---

## 5. Ce que la cible élimine de l'existant

| Existant | Sort dans la cible | Pourquoi |
|---|---|---|
| `enabled:` par stratégie + `disabled_slots` + top-N OOS | Remplacés par l'état/budget du bot | Trois vérités concurrentes → une seule |
| `max_positions`, `max_longs/shorts` globaux | Supprimés | Vetos qui brisent la fidélité au backtest ; le risque est borné par les budgets |
| Pauses CB par slot (3 pertes, WR floor, 5 trades/j) | Deviennent des entrées du score d'allocation | Les séries de pertes font partie de la distribution validée ; on n'agit que sur une sortie de fourchette |
| Volatility brake ×0.5 sur les entrées | Devient un paramètre d'allocation (réserve ↑ en régime volatil) | Même raison : ne pas dénaturer le sizing validé |
| Sizing live avec scaling DD dynamique | Supprimé au niveau bot (sizing = formule du backtest) | La divergence live/backtest disparaît par construction |
| Score OOS figé comme critère top-N | Forward-test glissant + réalisation live | Information fraîche et boucle refermée |
| Édition libre des paramètres d'une stratégie active | Re-optimisation → bot v2 | Un track record reste attaché à ses paramètres |

**Ce qui est conservé tel quel** : le moteur de backtest (exécution à l'open suivant, frais,
spread, borrow), le pipeline IS/OOS et la pénalité d'overfit, le trailing multi-phases, le
walk-forward et le Monte-Carlo (enfin branchés), la persistance des trades par stratégie/TF,
les notifications.

---

## 6. Mono-compte Binance : long, short et margin en parallèle

**Question : avec un unique compte Binance, plusieurs bots peuvent-ils gérer à la fois des
positions à la hausse, à la baisse et du margin pour certains ? → Oui**, car un compte Binance
est en réalité plusieurs portefeuilles étanches entre lesquels on transfère par API.

### 6.1 Ce que permet un compte unique

| Portefeuille | Long | Short | Levier | Isolation |
|---|---|---|---|---|
| Spot | ✅ | ❌ | ❌ | Aucune — solde commun |
| Margin **isolé** (un mini-wallet par paire) | ✅ | ✅ (emprunt) | ✅ ~5× | ✅ liquidation bornée au capital du wallet |
| Margin cross | ✅ | ✅ | ✅ | ❌ collatéral partagé — **à proscrire en multi-bots** |
| Futures USDⓈ-M (mode hedge) | ✅ | ✅ | ✅ | Long + short simultanés sur un symbole (non supporté par le code actuel) |

Alternatives : sous-comptes Binance (isolation parfaite, un bot = un sous-compte, mais réservé
corporate/VIP) ; futures hedge mode (non supporté aujourd'hui, le margin isolé couvre le besoin).

### 6.2 Le vrai problème : la comptabilité interne, pas Binance

L'exchange ne sait pas quel bot possède quoi : si deux bots détiennent du BTC spot, rien côté
Binance n'empêche l'un de vendre l'inventaire de l'autre. La protection est le registre interne —
exactement ce que les budgets virtuels apportent. Le code actuel a déjà la moitié du chemin
(`OpenPosition` en DB avec stratégie et taille, ventes dimensionnées sur la position du bot).

### 6.3 Mapping recommandé

- **Bots long spot** → wallet spot commun + registre interne (fonctionnement actuel).
- **Bots short / à levier** → **margin isolé sur leur paire** : le budget du bot devient
  *littéralement* le solde transféré dans son wallet isolé. « Un bot ne peut perdre que son
  budget » n'est plus une règle logicielle — c'est garanti par l'exchange (liquidation bornée
  au wallet, le reste du compte est intouchable).
- **Rebalance = transferts** spot ↔ wallets isolés par le méta-allocateur (API de transfert),
  au rythme quotidien déjà prévu.
- **Long et short simultanés sur le même symbole** (bot A long spot, bot B short margin) :
  techniquement sans conflit (deux wallets) ; économiquement un hedge qui paie frais + intérêts
  des deux côtés. **À autoriser** (deux TFs peuvent légitimement diverger ; l'interdire serait un
  veto brisant la fidélité au backtest), mais afficher l'**exposition nette par symbole** sur la
  page Portefeuille.

### 6.3 bis — Cas limite : long ET short *avec levier* sur la même paire

Le levier impose le margin, et Binance n'offre qu'**un wallet margin isolé par paire** : dans ce
wallet, le BTC acheté par le long compense comptablement le BTC emprunté par le short — les deux
positions se neutralisent, impossible de les tenir simultanément. Sur des **paires différentes**,
aucun problème (un wallet isolé chacun). Solutions pour la même paire, par ordre de
recommandation :

1. **Netting interne au méta-niveau (recommandé)** : les positions des bots restent virtuelles
   dans le registre ; l'exchange ne porte que la position **nette** (A short 0,10 + B long 0,04
   → short net 0,06). Chaque bot garde sa position virtuelle, son PnL, sa courbe d'équité.
   Économiquement supérieur : on ne paie pas les intérêts d'emprunt des deux côtés pour une
   exposition qui s'annule. Contrepartie : les stops exchange (`exchange_stop_orders`) deviennent
   des stops logiciels par bot, la position exchange ne correspondant plus à un bot unique.
2. **Paires de cotation différentes** : BTC/USDT et BTC/USDC = deux wallets isolés distincts →
   short levier sur l'une, long levier sur l'autre. Fonctionne sans développement, mais paie
   l'emprunt des deux côtés.
3. **Futures USDⓈ-M en mode hedge** : conçus pour le long+short simultané avec levier sur un
   même symbole — nécessite d'ajouter le support futures.
4. **Sous-comptes** (corporate/VIP).

Fenêtre de conflit étroite en pratique (deux bots opposés, avec levier, sur la même paire, au
même moment) ; le netting interne est l'extension naturelle du registre de budgets virtuels.

### 6.4 Adaptations du code

1. **Le venue devient un attribut du bot** : identité = (stratégie, TF, params, version,
   **venue** spot/margin-isolé). Aujourd'hui `margin_mode` et `max_leverage` sont globaux dans
   `config.yaml` — limite principale de l'existant pour ce scénario.
2. **Transferts de fonds au rebalance** (étendre `balance_sync.py` aux wallets margin par paire).
3. **Margin level surveillé par wallet isolé** (alertes 1.5 / critique 1.2 déclinées par bot).
4. Un seul processus, une seule clé API : les rate limits Binance sont par compte ; multiplier
   les processus n'apporte rien et crée des conflits.

---

## 7. Chemin de migration (incrémental, sans big bang)

1. **Forward-test glissant** : job quotidien qui re-backteste chaque slot actif sur les 30–60
   derniers jours avec ses params figés, stocke le score. Aucun impact trading — pure observation.
2. **Contrat de performance Monte-Carlo** à chaque apply + comparaison des trades réels. Affichage
   du cône sur une fiche bot embryonnaire. Toujours aucune décision automatique.
3. **Budgets virtuels par slot** : sizing sur budget du slot au lieu de l'équité globale ;
   suppression progressive des vetos globaux (d'abord en paper, mesurer la différence).
4. **Allocation automatique** pilotée par le score composite (d'abord en « shadow » : afficher ce
   que l'allocateur *aurait* fait, comparer, puis brancher).
5. **Cycle de vie complet + versioning** des bots.
6. **Refonte UI** (Portefeuille / Mes Bots / Laboratoire) — peut démarrer dès l'étape 2 puisque
   les visualisations clés (cône, forward-test) existent alors.

Chaque étape produit de la valeur seule et est réversible. Les étapes 1–2 sont les mêmes que les
chantiers prioritaires de l'analyse précédente : c'est la donnée qui rend tout le reste possible.
