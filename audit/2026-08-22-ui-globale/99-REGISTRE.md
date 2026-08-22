# 99 — Registre

Audit du **2026-08-22** sur `48189af` — interface complète : cohérence,
intégrité, non-redondance.

**11 constats** : 0 P0, 2 P1, 6 P2, 3 P3. **11 CONFIRMÉS par exécution**,
0 plausible.

> Preuves : [`02-CONSTATS.md`](02-CONSTATS.md) ·
> Causes et solutions : [`01-SYNTHESE.md`](01-SYNTHESE.md) ·
> Méthode : [`00-METHODE-ET-PERIMETRE.md`](00-METHODE-ET-PERIMETRE.md)

---

## Répartition

| Sévérité | Nombre | Dont reproduits |
|---|---:|---:|
| **P0** — bloquant | 0 | — |
| **P1** — majeur | 2 | 2 |
| **P2** — significatif | 6 | 6 |
| **P3** — mineur | 3 | 3 |

Par cause : **A** pas de domicile (4) · **B** vocabulaire (2) ·
**C** repli inventé (3) · hors cause (2).

---

## Constats

| ID | Sév. | Constat | Fichier | Cause | Statut |
|---|---|---|---|---|---|
| `UI-01` | **P1** | Le cache OHLCV (645 datasets) affiché sur `/data` et `/lab?tab=ml`, avec des colonnes qui ont divergé | `app/data/page.tsx` | A | ouvert |
| `UI-05` | **P1** | `/data` : « Première » et « Dernière » vides sur **645 lignes sur 645** — `all_stats` ne date pas les séries | `app/data/page.tsx` | C | ouvert |
| `UI-02` | P2 | `RecentMlJobs` et `MLVersioningAudit` montés sur `/models` et `/lab?tab=ml` | `app/models/page.tsx` | A | ouvert |
| `UI-03` | P2 | `OptimizerView` monté deux fois dans le même jeu d'onglets | `app/lab/page.tsx` | A | ouvert |
| `UI-04` | P2 | `/settings` porte des onglets « Données » et « Audit » qui redoublent des pages entières | `app/settings/page.tsx` | A | ouvert |
| `UI-06` | P2 | `completeness` et `gaps` reçus par `/data`, jamais affichés | `app/data/page.tsx` | C | ouvert |
| `UI-07` | P2 | « 40 stratégies » (actives) vs « 41 » (optimisables) — même mot, deux ensembles | `app/portfolio/page.tsx` | B | ouvert |
| `UI-08` | P2 | « 3 recettes » (au registre) vs « 10 » (du dépôt) — même mot, deux ensembles | `app/models/page.tsx` | B | ouvert |
| `UI-10` | P2 | La devise du portefeuille vient de `venues[0]` : 1 000 USDC affichés « 1 000,00 € » | `app/portfolio/page.tsx:76` | C | ouvert |
| `UI-09` | P3 | Quatre identifiants de tickets internes rendus en badges visibles | `app/trades/page.tsx:230` | — | ouvert |
| `UI-11` | P3 | Cinq routes jamais appelées, dont trois écritures de configuration | `config_risk.py` | — | ouvert |

---

## Ce qui a été vérifié et ne pose pas de problème

- **Les composants partagés le sont proprement.** Les trois composants de
  courbe d'équité (`EquityChart`, `BacktestEquityChart`, `EquityCurve`) sont un
  composant commun et deux enveloppes fines, refactor documenté en tête de
  fichier. Réutilisation, pas redondance — contrôlé avant d'écrire.
- **99 routes sur 104 sont consommées** par le front.
- **Les 10 pages répondent en 200** et rendent du contenu.
- **La navigation n'a aucune entrée morte** : dix liens, quatre groupes, tous
  aboutissent.
- **Le contrat typé tient** là où il a été posé : les modèles du Laboratoire
  sont dans `generated.ts` et sous le test de dérive.

## Note de méthode

Mon premier recensement des routes non appelées en donnait **13**. Après
vérification une par une, il en reste **5** : ma normalisation des
`{slot_key:path}` produisait huit faux positifs. Le chiffre publié est celui
d'après contrôle.
