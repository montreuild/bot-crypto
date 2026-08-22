# 02 — Constats

11 constats, **tous CONFIRMÉS par exécution**.

Cause : [`A`](01-SYNTHESE.md#cause-a--aucun-concept-na-de-domicile-déclaré) domicile ·
[`B`](01-SYNTHESE.md#cause-b--un-mot-deux-ensembles) vocabulaire ·
[`C`](01-SYNTHESE.md#cause-c--lécran-choisit-ce-que-le-serveur-na-pas-dit) repli inventé.

---

## UI-01 — Le cache OHLCV est affiché deux fois, avec des colonnes qui ont divergé (P1, CONFIRMÉ, cause A)

**Fichiers** : `frontend/src/app/data/page.tsx`,
`frontend/src/components/views/ml-view.tsx:99`.

Les mêmes 645 datasets sont rendus par deux tables, sur deux pages :

| Page | Lignes | Colonnes |
|---|---:|---|
| `/data` | 645 | `Symbole · TF · Bougies · Première · Dernière · Taille · Actions` |
| `/lab?tab=ml` | 645 | `Symbole · TF · Bougies · Complétude · Trous · Taille` |

**Scénario d'échec** — l'opérateur veut savoir si `AC.PA/1h` est exploitable. Sur
`/data` il lit 4 701 bougies et deux tirets. Sur `/lab` il lit 4 701 bougies,
100 % de complétude et 0 trou. Deux réponses, deux pages, sans que rien
n'indique laquelle fait autorité.

La divergence est **récente et documentée** : `LAB-01`/`LAB-09` ont corrigé la
table du Laboratoire il y a quelques heures ; celle de `/data`, qui montre la
même chose, n'a pas suivi. C'est exactement ce que l'absence de domicile
produit.

**Vérification** — comptage dans le navigateur sur le backend Docker :
645 lignes des deux côtés, en-têtes relevés tels quels.

---

## UI-02 — Jobs ML et audit de versioning montés sur deux pages (P2, CONFIRMÉ, cause A)

**Fichiers** : `frontend/src/app/models/page.tsx`,
`frontend/src/components/views/ml-view.tsx`.

`<RecentMlJobs>` et `<MLVersioningAudit>` sont montés **à l'identique** depuis
`/models` et depuis l'onglet ML de `/lab`.

**Scénario d'échec** — un entraînement échoue. L'opérateur le voit sur `/lab`,
va sur `/models` pour comprendre, y retrouve la même table sans information
supplémentaire, et ne sait pas si les deux vues sont synchronisées ou si l'une
est un cache.

**Vérification** — relevé des montages :

```
RecentMlJobs         components/views/ml-view.tsx
RecentMlJobs         app/models/page.tsx
MLVersioningAudit    components/views/ml-view.tsx
MLVersioningAudit    app/models/page.tsx
```

Puis comptage dans le navigateur : la table `Type|Stratégie|Symbole|TF|Statut|
Début|Résultat|Actions` apparaît sur les deux pages.

---

## UI-03 — L'optimiseur est monté deux fois dans le même jeu d'onglets (P2, CONFIRMÉ, cause A)

**Fichiers** : `frontend/src/app/lab/page.tsx`,
`frontend/src/components/views/ml-view.tsx`.

`/lab` a six onglets. `<OptimizerView>` est monté par l'onglet `Optimizer`
**et** par l'onglet `ML Train` (sous le titre « Optimiseur ML »).

**Scénario d'échec** — l'opérateur lance une optimisation depuis l'onglet ML,
puis cherche son job dans l'onglet Optimizer. Il l'y trouve, ou pas, selon le
filtre `filterMl` — deux formulaires, deux listes de jobs, un seul moteur.

C'est aussi ce qui a produit `LAB-10` : le filtre passait à deux enfants sur
trois. Un montage unique avec un filtre n'aurait pas eu de troisième enfant à
oublier.

**Vérification** — relevé des montages, et présence des deux formulaires
constatée dans le navigateur.

---

## UI-04 — `/settings` redouble deux pages entières (P2, CONFIRMÉ, cause A)

**Fichier** : `frontend/src/app/settings/page.tsx`.

`/settings` porte sept onglets : `Capital · Risque · Stratégies · Notifs ·
Données · Audit · UI`. Deux d'entre eux nomment des pages qui existent par
ailleurs (`/data`, `/audit` et `/audit-log`), et « Risque » édite les
enveloppes que `/portfolio` affiche.

**Scénario d'échec** — l'opérateur cherche à purger le cache OHLCV. Rien ne lui
dit si c'est dans `/data` ou dans `/settings › Données`. Les deux existent.

**Vérification** — onglets relevés dans le navigateur ; `useRisk` consommé par
`/portfolio` et `/settings`, mesuré par parcours des imports.

---

## UI-05 — `/data` : deux colonnes vides sur 645 lignes (P1, CONFIRMÉ, cause C)

**Fichiers** : `frontend/src/app/data/page.tsx`,
`app/core/candle_store.py` (`all_stats`).

Les colonnes « Première » et « Dernière » sont alimentées par `from`/`to`, que
l'inventaire complet **ne date jamais** : `all_stats` compte les barres sans
charger les DataFrames — c'est ce qui lui permet de balayer 645 fichiers en un
temps acceptable. Les deux champs valent `None` par construction.

**Scénario d'échec** — l'opérateur veut savoir jusqu'à quand va sa série
`BNP.PA/1h`. Deux colonnes lui promettent la réponse ; elles sont vides pour
tous les datasets, sans distinguer « pas de donnée » de « pas mesuré ».

**Vérification** — mesuré dans le navigateur :

```
{"lignes_sans_date": 645, "total": 645}
AC.PA | 15m | 2 039 | — | — | 30.1 KB | Analyser
```

645 sur 645. Le même défaut a été corrigé sur `/lab` (`LAB-09`) ; il subsiste
ici, ce qui est le symptôme de [`UI-01`](#ui-01).

---

## UI-06 — Complétude et trous reçus par `/data`, jamais affichés (P2, CONFIRMÉ, cause C)

**Fichier** : `frontend/src/app/data/page.tsx`.

La réponse porte `completeness` et `gaps` par dataset — issus du lot `DOWN-02`,
précisément pour informer l'opérateur de la fiabilité d'une série. La page qui
s'appelle « Données OHLCV » ne les montre pas.

**Scénario d'échec** — deux séries de 4 701 barres, l'une complète, l'autre à
82 % avec 12 trous : indiscernables sur la page dédiée aux données.

**Vérification** — réponse réelle de `/api/candles/stats` :
`{"symbol": "AC.PA", "tf": "15m", "bars": 2039, …, "completeness": 1.0, "gaps": 0}`,
et colonnes relevées dans le navigateur.

---

## UI-07 — « 40 stratégies » ici, « 41 » là (P2, CONFIRMÉ, cause B)

**Fichiers** : `frontend/src/app/portfolio/page.tsx`,
`frontend/src/components/optimizer/optimizer-config-form.tsx`.

Le bandeau de `/portfolio` rend
`` `Vue consolidée · ${status.timeframes?.length} TFs · ${status.strategies?.length} stratégies` ``
— les stratégies **actives** du trader. `/lab` en annonce 41 : les stratégies
**optimisables**. Aucun des deux libellés ne le précise.

**Scénario d'échec** — l'opérateur lit « 40 stratégies » sur l'accueil, en
compte 41 dans le sélecteur d'optimisation, et cherche la stratégie manquante.
Il n'en manque aucune : ce ne sont pas les mêmes ensembles.

**Vérification** — `/api/optimize/spaces` rend 41 entrées ; le bandeau lit
`status.strategies`, relevé à 40 dans le navigateur.

---

## UI-08 — « 3 recettes » ici, « 10 recettes » là (P2, CONFIRMÉ, cause B)

**Fichiers** : `frontend/src/app/models/page.tsx`,
`frontend/src/components/cards/ml-recipes-list.tsx`.

`/models` annonce « 3 recettes · 2 alertes » : les couples (timeframe, recette)
**présents au registre**. `/lab?tab=ml` annonce 10 recettes : celles **déclarées
dans le dépôt**.

**Scénario d'échec** — l'opérateur veut entraîner `stat48_v5`. Il la voit sur
`/lab`, ne la trouve pas sur `/models`, et conclut à une perte de données. Elle
n'a simplement jamais été entraînée.

**Vérification** — `/api/ml/registry` rend 3 entrées, `/api/ml/recipes` en rend
10. Les deux réponses sont exactes.

---

## UI-09 — Quatre identifiants de tickets internes rendus en badges (P3, CONFIRMÉ)

**Fichiers** : `app/trades/page.tsx:230`,
`components/cards/train-recipe-dialog.tsx:295`,
`components/cards/strategy-params-panel.tsx:58`,
`components/cards/ml-leakage-checker.tsx:111`.

Quatre références de suivi interne sont rendues comme `<Badge>` visibles :

```
Slot (strategy::tf·paire)  [UI-04]
Pool multi-symboles        [ML-16]
                           [P0-3]
                           [P1-1]
```

**Scénario d'échec** — l'opérateur lit « UI-04 » à côté d'un champ et cherche ce
que cela désigne. C'est un numéro de tâche, sans signification pour lui.

**Vérification** — recherche sur le **texte rendu** uniquement, commentaires et
docstrings exclus par analyse ligne à ligne : 4 occurrences, listées ci-dessus.

---

## UI-10 — La devise du portefeuille vient de l'ordre d'un tableau (P2, CONFIRMÉ, cause C)

**Fichiers** : `frontend/src/app/portfolio/page.tsx:76`,
`frontend/src/components/layout/topbar.tsx:18`,
`frontend/src/lib/utils.ts:76`.

```typescript
const displayCcy = quoteCurrency(risk?.venues?.[0]);
```

Le capital consolidé est libellé avec la devise de la **première venue rendue**
par l'API, et `quoteCurrency` retombe sur `'USD'` à défaut.

**Scénario d'échec** — deux venues déclarées : `euronext-paper` (EUR,
enveloppe 10 000) et `margin-isolated` (USDC, enveloppe 1 000). `/api/status`
rend `capital: 1000.0` et `quote_currency: null`. L'écran affiche
**« 1 000,00 € »** — un montant en USDC portant un symbole euro, choisi parce
que la venue euronext arrive en premier dans le tableau. Réordonner les venues
change la devise affichée sans que rien n'ait bougé côté trading.

**Vérification** — réponses réelles :

```
/api/status  capital = 1000.0 | quote_currency = None
/api/risk    venues[0] = euronext-paper (envelope 10000)
             venues[1] = margin-isolated (envelope 1000)
```

et « 1 000,00 € » relevé dans le bandeau et la barre supérieure.

---

## UI-11 — Cinq routes ne sont jamais appelées, dont trois écritures de config (P3, CONFIRMÉ)

**Fichiers** : `app/api/routes/config_risk.py`, `config_global.py`,
`config_strategies.py`, `ws.py`.

| Route | Fichier |
|---|---|
| `POST /api/config/risk` | `config_risk.py` |
| `POST /api/config/margin` | `config_global.py` |
| `POST /api/config/auto-optimizer` | `config_strategies.py` |
| `GET /api/config/strategy-overrides` | `config_strategies.py` |
| `GET /api/ws/status` | `ws.py` |

**Scénario d'échec** — une écriture de configuration sans appelant est du code
qui n'est jamais exercé hors tests. Elle reste exposée, protégée par la clé
d'API, et personne ne sait si elle fonctionne encore. `POST /api/config/risk`
est le cas le plus net : `/settings` édite bien le risque, mais par une autre
route.

**Vérification** — les 104 routes confrontées au front, puis chaque candidate
vérifiée séparément. 13 candidates au premier passage, **5 après contrôle** :
les 8 autres étaient des faux positifs de ma normalisation des
`{slot_key:path}`.
