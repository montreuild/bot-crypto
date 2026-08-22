# 01 — Synthèse : trois causes, onze constats

**11 constats, tous CONFIRMÉS par exécution** : 2 P1, 6 P2, 3 P3.
Détail et preuves dans [`02-CONSTATS.md`](02-CONSTATS.md).

L'interface n'a pas onze défauts indépendants. Elle a **trois manques de
structure**, et chacun produit ses symptômes en série.

---

## Cause A — Aucun concept n'a de domicile déclaré

Six notions vivent à deux endroits. Pas par duplication de code — les
composants sont correctement partagés — mais parce que **rien ne dit où une
notion habite**, alors chaque page utile se la réapproprie.

| Notion | Domiciles | Constat |
|---|---|---|
| Cache OHLCV (645 datasets) | `/data` **et** `/lab?tab=ml` | [`UI-01`](02-CONSTATS.md#ui-01) |
| Jobs ML, audit de versioning | `/models` **et** `/lab?tab=ml` | [`UI-02`](02-CONSTATS.md#ui-02) |
| Optimiseur | onglet `Optimizer` **et** onglet `ML Train` | [`UI-03`](02-CONSTATS.md#ui-03) |
| Données, Audit | pages `/data`, `/audit` **et** onglets de `/settings` | [`UI-04`](02-CONSTATS.md#ui-04) |
| Enveloppes de risque | `/portfolio` (lecture) **et** `/settings` (édition) | [`UI-04`](02-CONSTATS.md#ui-04) |

Le coût n'est pas l'écran en double : c'est que **les deux copies divergent**.
Le cache OHLCV en est la démonstration — la même table, les mêmes 645 lignes,
et des colonnes différentes depuis que l'une a été corrigée et pas l'autre.

### Solution globale

Un **modèle de navigation explicite**, écrit une fois : chaque notion a une
page propriétaire, les autres endroits y **renvoient** au lieu de la
réafficher.

| Notion | Propriétaire | Ailleurs |
|---|---|---|
| Données OHLCV | `/data` | un résumé chiffré + lien |
| Modèles, jobs ML, versioning | `/models` | un résumé + lien |
| Optimisation | onglet `Optimizer` | filtre ML dans le même onglet, pas un second montage |
| Risque et enveloppes | `/settings` pour l'édition | `/portfolio` en lecture seule, ce qui est déjà le cas |

Corollaire : `/settings` cesse d'être une deuxième table des matières. Ses
onglets « Données » et « Audit » deviennent des liens.

Le test qui empêche la récidive existe déjà en germe ailleurs
(`test_dette04_taille_fichiers`) : une liste explicite de qui monte quoi, avec
les exceptions écrites et justifiées.

---

## Cause B — Un mot, deux ensembles

Trois comptages différents portent le même nom sur des pages différentes, sans
qualificatif.

| Mot | Ici | Là | Constat |
|---|---|---|---|
| « stratégies » | **40** sur `/portfolio` (actives) | **41** sur `/lab` (optimisables) | [`UI-07`](02-CONSTATS.md#ui-07) |
| « recettes » | **3** sur `/models` (au registre) | **10** sur `/lab` (du dépôt) | [`UI-08`](02-CONSTATS.md#ui-08) |

Aucun de ces chiffres n'est faux. C'est leur **juxtaposition sans qualificatif**
qui trompe : l'opérateur passe d'un écran à l'autre et croit à une incohérence
de données là où il n'y a qu'une incohérence de vocabulaire.

C'est le même mécanisme que `LAB-07` (recettes et stratégies empilées sans
lien), traité dans l'audit du Laboratoire — la cause n'avait pas été cherchée
au-delà de l'onglet ML.

### Solution globale

Le compteur porte **ce qu'il compte**, pas la catégorie. « 40 stratégies
actives », « 41 stratégies optimisables », « 3 recettes au registre »,
« 10 recettes disponibles ». Quatre libellés, plus aucune contradiction
apparente.

C'est déjà ce qui a été fait pour `LAB-06` (« 1/14 **stratégies** entraînées »).
La règle mérite d'être posée plutôt que réappliquée au cas par cas.

---

## Cause C — L'écran choisit ce que le serveur n'a pas dit

Quand une donnée manque, l'UI comble — silencieusement, et parfois faux.

**La devise du portefeuille vient de l'ordre d'un tableau.**
`quoteCurrency(risk?.venues?.[0])` : le capital consolidé est libellé avec la
devise de la **première venue rendue**. Avec `euronext-paper` (EUR) et
`margin-isolated` (USDC), un montant de 1 000 USDC s'affiche « 1 000,00 € ». Le
serveur, lui, rend `quote_currency: null` — il ne prétend rien
([`UI-10`](02-CONSTATS.md#ui-10)).

**Deux colonnes ne peuvent rien afficher.** `/data` montre « Première » et
« Dernière » pour 645 lignes sur 645 vides : l'inventaire complet ne date pas
les séries, par construction. Pendant ce temps la complétude et les trous, que
la même réponse porte, ne sont montrés nulle part sur cette page
([`UI-05`](02-CONSTATS.md#ui-05), [`UI-06`](02-CONSTATS.md#ui-06)).

**Cinq routes ne sont jamais appelées**, dont trois écritures de configuration
([`UI-11`](02-CONSTATS.md#ui-11)).

### Solution globale

**Ne rien inventer, et le dire quand on ne sait pas.** Une devise absente
s'affiche comme absente, pas comme un euro. Une colonne que la source ne
remplit jamais n'est pas une colonne.

Concrètement, deux règles :

1. Un repli ne se choisit **jamais par l'ordre d'un tableau**. Soit le serveur
   déclare la devise consolidée, soit l'UI affiche le montant sans unité.
2. Une colonne se justifie par un champ que la route **remplit** — la même
   discipline que le contrat typé posé au lot précédent, appliquée au rendu.

---

## Ce que le backend offre déjà et que l'UI n'utilise pas

| Route | État |
|---|---|
| `POST /api/config/risk` | jamais appelée |
| `POST /api/config/margin` | jamais appelée |
| `POST /api/config/auto-optimizer` | jamais appelée |
| `GET /api/config/strategy-overrides` | jamais appelée |
| `GET /api/ws/status` | jamais appelée |

Sur 104 routes, **99 sont consommées**. Ce n'est pas un problème de couverture :
c'est cinq points précis à trancher — soit les exposer, soit les retirer. Les
laisser dans cet état, c'est entretenir une API dont on ne sait plus ce qui
sert.

---

## Ce qui va

Consigné parce qu'un audit qui ne dit que ce qui ne va pas laisse croire que
le reste n'a pas été regardé.

- **Les composants partagés le sont proprement.** Les trois composants de
  courbe d'équité sont deux enveloppes fines autour d'un `EquityChart` commun,
  avec l'historique du refactor en tête de fichier. C'est de la réutilisation,
  pas de la redondance — vérifié avant d'écrire quoi que ce soit.
- **99 routes sur 104 sont consommées.**
- **Les 10 pages répondent en 200** et rendent du contenu.
- **La navigation est cohérente** : dix entrées, trois groupes (Trading,
  Recherche, Données, Configuration), aucune entrée morte.

---

## Ordre proposé

1. **Cause B d'abord** — quatre libellés à qualifier. C'est l'effort le plus
   faible pour le gain de lisibilité le plus direct, et sans risque.
2. **Cause C ensuite** — la devise et les colonnes vides sont des erreurs
   d'affichage à corriger, indépendantes l'une de l'autre.
3. **Cause A en dernier** — c'est la seule qui demande une décision de
   structure, donc la seule qui mérite d'être discutée avant d'être faite.
