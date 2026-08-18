# Revue complète — méthode et registre des constats

**Date** : 2026-08-18 · **HEAD audité** : `3ca68a3` · **Branche** : `claude/crypto-bot-audit-f7043f`

---

## 1. Ce qui a été audité

| Périmètre | Volume |
|---|---|
| `app/` (api, core, engine, live, ml, strategies) | ~61 400 lignes Python |
| `frontend/src` | ~32 700 lignes TS/TSX, 162 fichiers |
| `tests/` | ~29 100 lignes |
| `cli.py`, `config/`, Docker, CI | — |

**Hors périmètre, par consigne** : `scripts/`, `research/`, `optimize_runner.py` (scripts
indépendants) ; `docs/`, `audit/` (revue précédente), `CHANGELOG.md`, `README.md`,
`ARCHITECTURE.md`, `PRODUCTION_READINESS.md`.

**Les commentaires de code ne sont pas une source de vérité.** Plusieurs constats de ce
rapport portent précisément sur un écart entre ce qu'un commentaire ou une docstring
affirme et ce que le code fait. Vérification faite dans les deux sens :
la docstring de `BacktestResult.to_dict` affirme `total_pnl == net_profit` — c'est faux
dès qu'un trade a des jambes partielles (FIN-01, reproduit).

Seule dépendance vérifiée vers le hors-périmètre : **aucun module de `app/` n'importe
`scripts/` ni `research/`** (`grep -rE '^\s*(from|import)\s+(scripts|research)' app/ cli.py`
→ vide). L'inverse est vrai 41 fois, ce qui est le sens attendu.

---

## 2. Méthode

1. **Graphe de code** — `code-review-graph`, reconstruction complète : 669 fichiers,
   6 490 nœuds, 64 181 arêtes, 487 flux d'exécution, 15 communautés.
2. **Revue du delta 3 jours** — 86 commits depuis le 2026-08-15, 243 fichiers,
   +15 067 / −18 154 lignes.
3. **Lecture du code**, fichier par fichier sur les chemins critiques (exécution, coûts,
   PnL, risque, optimiseur, live).
4. **Vérification exécutable** — outillage réel du dépôt lancé sur le worktree :

   | Outil | Résultat |
   |---|---|
   | `pytest -m "not slow"` (+ couverture) | **2 075 passés, 27 skipped, 18 deselected** — 153 s |
   | Couverture `app/` | **66 %** (28 364 instructions, 9 620 non couvertes) |
   | `ruff check .` (config du dépôt) | **propre** |
   | `ruff` règles étendues (`B,SIM,RUF,PERF,C4,PIE,RET,PTH`) | ~900 signalements |
   | `mypy app` | **1 084 erreurs dans 120 fichiers** (206 fichiers analysés) |
   | `tsc --noEmit` | **0 erreur** |
   | `vitest run` | **126 tests, 10 fichiers, tous passés** |
   | Couverture `frontend/src` | **4,84 %** |
   | `eslint .` | **0 erreur, 5 avertissements** |

5. **Reproduction** — les constats marqués `CONFIRMÉ` ont été reproduits par exécution,
   en réutilisant le harnais de test du dépôt (`tests/test_partial_exits.py`).

---

## 3. Conventions du registre

**Sévérité**

| Niveau | Sens |
|---|---|
| **P0** | Bloquant — perte d'argent, corruption de données, ou faille exploitable. Ne pas passer en production. |
| **P1** | Majeur — un chiffre affiché ou une décision automatique est faux ; l'utilisateur ne peut pas le savoir. |
| **P2** | Mineur — écart réel mais borné, ou dette qui coûtera cher. |
| **P3** | Cosmétique, ou risque latent sans appelant fautif aujourd'hui. |

**Statut de preuve**

- `CONFIRMÉ` — reproduit par exécution, chiffres à l'appui.
- `PLAUSIBLE` — établi par lecture du code, non exécuté (chemin coûteux à monter).

**Identifiants** — `DOMAINE-nn`. ⚠ Ces identifiants sont **propres à cette revue** et
n'ont aucun rapport avec ceux de `audit/` (revue précédente : `F-01`, `B-02`, `S11`…),
même quand les préfixes se ressemblent.

---

## 4. Rapports

| Fichier | Domaine |
|---|---|
| `00-METHODE-ET-REGISTRE.md` | ce document |
| `01-SYNTHESE.md` | synthèse exécutive, constats classés |
| `02-DELTA-3-JOURS.md` | revue de code des 86 commits du 15 au 18/08 |
| `03-ARCHITECTURE.md` | architecture, couplage, chokepoints, dette structurelle |
| `04-FINANCIER.md` | coûts, PnL, frais, emprunt, funding, risque |
| `05-BACKTEST.md` | moteur de backtest, métriques, biais |
| `06-OPTIMISEUR.md` | recherche, scoring, sur-apprentissage, gates |
| `07-LIVE-EXECUTION.md` | trading live, exécution, réconciliation |
| `08-ML.md` | entraînement, fuite temporelle, registre de modèles |
| `09-BACKEND-API.md` | FastAPI, contrats, erreurs, concurrence |
| `10-FRONTEND.md` | Next.js, état, contrats de types, robustesse |
| `11-UI-UX.md` | ergonomie, accessibilité, lisibilité des chiffres |
| `12-DONNEES.md` | OHLCV, feature store, intégrité, calendriers |
| `13-PERFORMANCE.md` | coûts de calcul, allocations, parallélisme |
| `14-SECURITE.md` | authentification, secrets, surface d'exposition |
| `15-TESTS-CI.md` | couverture, qualité des tests, pipeline |
