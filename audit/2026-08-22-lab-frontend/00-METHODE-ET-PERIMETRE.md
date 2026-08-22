# 00 — Méthode et périmètre

Audit du **2026-08-22** sur `d77e9f3`, déclenché par un constat d'usage :
le Laboratoire n'est pas ergonomique pour l'entraînement ML, l'optimiseur et
le backtest, avec « des erreurs ou des ambiguïtés » — l'exemple donné étant la
liste de stratégies pour l'entraînement d'une recette.

## Périmètre

**Dans le périmètre** — les trois onglets de `/lab` et ce qui les alimente :

| Couche | Fichiers |
|---|---|
| Vues | `ml-view.tsx`, `optimizer-view.tsx`, `backtest-view.tsx` |
| Cartes et formulaires | `train-recipe-dialog.tsx`, `ml-recipes-list.tsx`, `optimizer-config-form.tsx`, `optimizer/*` |
| Contrat client | `frontend/src/lib/api.ts`, `frontend/src/types/` |
| Routes servantes | `app/api/routes/ml.py`, `optimizer.py`, `backtest.py`, `data.py` |

**Hors périmètre** : la page `/models` (registre versionné, gate de
promotion), les autres onglets de `/lab` (Replay, Multi-TF, Compare), et le
moteur lui-même — l'audit porte sur **ce que l'UI dit du moteur**, pas sur ce
que le moteur calcule.

## Règle de preuve

Reprise de la revue du 20 août, inchangée : chaque constat porte un
identifiant stable, une sévérité, une référence `fichier:ligne`, un **scénario
d'échec concret** (entrée → sortie fausse) et la **vérification effectuée**.

Rien n'est fondé sur un commentaire ou une documentation. Un constat est
**CONFIRMÉ** quand il a été reproduit — appel HTTP réel sur le conteneur, ou
mesure dans le navigateur — et **PLAUSIBLE** quand il ne repose que sur la
lecture.

Les 12 constats de ce rapport sont **tous CONFIRMÉS**. Aucun n'est plausible :
ce qui n'a pas pu être reproduit n'a pas été écrit.

## Méthode

1. **Recensement croisé.** Toutes les routes `/api/ml`, `/api/optimize`,
   `/api/backtest` (32) confrontées à ce que `api.ts` appelle. Résultat : aucune
   route morte, aucun appel vers une route inexistante. La piste « le front
   n'utilise pas le backend » est donc écartée d'emblée — le problème est dans
   ce qui est **échangé**, pas dans ce qui est **appelé**.

2. **Comparaison paramètre par paramètre.** Chaque formulaire confronté à la
   signature de sa route. Le backtest expose ses 12 paramètres, l'optimiseur ses
   12. L'entraînement ML, non — c'est là que ça casse.

3. **Rejeu HTTP sur le conteneur en marche** (`crypto-bot-api`), avec le
   payload que l'UI construit réellement, pas celui qu'elle prétend construire.

4. **Mesure dans le navigateur** sur le backend réel — serveur de
   développement en 3100, le stack Docker de l'opérateur occupant 3000 et 8000.
   C'est ainsi qu'on compte 5 160 lignes là où on en attend 645.

5. **Recul.** Les constats sont regroupés par **cause**, pas par symptôme —
   c'est l'objet de [`01-SYNTHESE.md`](01-SYNTHESE.md). Douze correctifs isolés
   laisseraient le treizième arriver par la même porte.

## Ce que l'audit ne dit pas

Il ne juge ni la valeur des trois outils, ni la justesse des calculs qu'ils
déclenchent. Il constate que **l'écran ne rend pas compte de ce que le moteur
fait** — et que le moteur, lui, produit déjà l'information manquante.
