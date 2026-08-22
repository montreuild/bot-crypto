# 00 — Méthode et périmètre

Audit du **2026-08-22** sur `48189af`, portant sur **toute l'interface** :
cohérence, intégrité et non-redondance des pages et des onglets, plus ce que
le backend met à disposition sans que l'UI s'en serve.

Il fait suite à l'audit du Laboratoire du même jour
([`2026-08-22-lab-frontend`](../2026-08-22-lab-frontend/01-SYNTHESE.md)), dont
les 10 constats sont livrés. Celui-ci élargit à l'ensemble.

## Périmètre

**Dans le périmètre** — 10 pages, 122 composants, 32 382 lignes de front :

```
/portfolio  /bots  /trades  /lab  /market
/audit  /audit-log  /data  /models  /settings
```

et les 104 routes de `app/api/` qui les alimentent.

**Hors périmètre** : le moteur, les calculs, la logique de trading. L'audit
porte sur **ce que l'écran dit du système**, pas sur ce que le système calcule.
Un chiffre juste mal présenté est dans le périmètre ; un chiffre faux ne l'est
pas — il relève des revues précédentes.

## Règle de preuve

Inchangée depuis la revue du 20 août : identifiant stable, sévérité, référence
`fichier:ligne`, **scénario d'échec concret** et **vérification effectuée**.

Un constat est **CONFIRMÉ** quand il a été reproduit — mesure dans le
navigateur sur le backend réel, ou appel HTTP — et **PLAUSIBLE** quand il ne
repose que sur la lecture.

Les 11 constats sont **tous CONFIRMÉS**. Rien de supposé n'a été écrit.

## Méthode

1. **Inventaire des pages et de la navigation**, relevé dans le navigateur
   plutôt que déduit du code — les onglets sont déclarés de quatre façons
   différentes selon les pages, un `grep` en aurait manqué.

2. **Couverture des routes.** Les 104 routes confrontées à ce que le front
   référence. 13 candidates « jamais appelées », ramenées à **5 réelles** après
   vérification une par une : mon premier filtre normalisait mal les
   `{slot_key:path}` et produisait 8 faux positifs.

3. **Recherche de redondance par les données, pas par les écrans.** Quels
   hooks alimentent quelles pages : deux pages qui consomment le même hook
   montrent la même chose. C'est ce qui a fait sortir les recouvrements que la
   navigation ne laisse pas deviner.

4. **Recherche de redondance par les composants.** Quels composants sont
   montés depuis plusieurs endroits. Attention : un composant partagé est de la
   **réutilisation**, pas de la redondance — les trois composants de courbe
   d'équité se sont révélés être deux enveloppes fines autour d'un composant
   commun, donc rien à signaler. La redondance est ailleurs : le **même contenu
   sur deux pages**.

5. **Mesure dans le navigateur** sur le backend Docker de l'opérateur, page par
   page : comptage des lignes de tableau, lecture des cellules, relevé des
   libellés.

6. **Recul.** Les constats sont regroupés par cause dans
   [`01-SYNTHESE.md`](01-SYNTHESE.md). Onze correctifs isolés laisseraient le
   douzième arriver par la même porte.

## Ce que l'audit ne dit pas

Il ne propose pas de refonte graphique et ne juge pas l'esthétique. Il traite
trois questions vérifiables : **la même chose est-elle dite deux fois ?**,
**ce qui est affiché est-il exact ?**, **ce que le serveur sait arrive-t-il à
l'écran ?**
