# 02 — Architecture

Graphe reconstruit intégralement le 2026-08-20 sur `bb94993` :
**714 fichiers, 6 818 nœuds, 67 162 arêtes, 15 communautés, 505 flux d'exécution**.

## 1. Structure en communautés

| Communauté | Taille | Cohésion | Langage dominant |
|---|---:|---:|---|
| `tests-fetch` | 2 872 | 0,221 | python |
| `cards-use` (frontend) | 815 | 0,246 | tsx |
| `core-fetch` (`app/core`) | 705 | 0,133 | python |
| `strategies-strategy` | 431 | 0,154 | python |
| `ml-predict` | 288 | 0,119 | python |
| `engine-job` | 286 | 0,105 | python |
| `routes-ml` (`app/api`) | 263 | 0,070 | python |
| `scripts-cfg` | 159 | 0,091 | python |
| `live-position` | 137 | 0,130 | python |
| `research-section` | 82 | 0,164 | python |

Le découpage détecté **recoupe fidèlement l'arborescence** `app/core`,
`app/engine`, `app/live`, `app/ml`, `app/api`, `app/strategies`. C'est le signe
d'une séparation en couches réelle, pas seulement déclarative.

**Sens des dépendances vérifié** — les arêtes vers `scripts-cfg` (69 depuis
`engine-job`, 54 depuis `ml-predict`, 40 depuis `core-fetch`) et vers
`research-section` (47, 13, 11) sont orientées **scripts/research → app**.
Aucun module de `app/` n'importe `scripts/` ni `research/` (vérification dans
`00-METHODE-ET-PERIMETRE.md`). Le sens est correct.

**Cohésion faible partout (0,07 – 0,25).** Le point le plus bas est
`routes-ml` (0,070) : les routes API délèguent beaucoup et partagent peu entre
elles — attendu pour une couche de transport. `engine-job` à 0,105 est plus
gênant : le moteur devrait être plus autoportant qu'il ne l'est.

## 2. Hubs — rayon d'impact

| Nœud | Degré (in / out) | Lecture |
|---|---:|---|
| `app/ml/backend/features.py::build_features` | 366 (12 / 354) | Constructeur de features : appelle presque tout `core`. Sortance extrême. |
| `app/engine/backtest.py::Backtester.run` | 259 (89 / 170) | **Le vrai cœur.** 89 entrants : tout converge ici. |
| `frontend/…/smart-replay-view.tsx::SmartReplayView` | 239 (2 / 237) | Composant-monolithe (744 lignes). |
| `frontend/src/lib/utils.ts::cn` | 201 (197 / 4) | Utilitaire de classes — sain. |
| `app/engine/backtest.py::Backtester` | 157 (94 / 63) | — |
| `app/core/candle_store.py::CandleStore.fetch` | 146 (47 / 99) | Point d'entrée données unique. |
| `app/api/helpers.py::verify_api_key` | 130 (115 / 15) | **115 entrants : l'auth est appliquée systématiquement.** Bon signe. |
| `app/engine/position_lifecycle.py::_try_enter` | 130 (1 / 129) | Entrée en position, très sortante. |

`Backtester.run` cumule le plus fort degré entrant du backend **et** la deuxième
centralité d'intermédiarité (0,0107). C'est le point de passage unique du
dépôt : toute régression y est globale. C'est exactement là que se situent
`FIN-01`, `FIN-02` et `BT-01` (voir `04-MOTEUR-FINANCIER.md`, `05-BACKTEST.md`).

## 3. Points de passage (betweenness)

| Nœud | Betweenness |
|---|---:|
| `frontend/e2e/tests/a11y.spec.ts` (test) | 0,0188 |
| `app/engine/backtest.py::Backtester.run` | 0,0107 |
| `app/live/live_trader.py::LiveTrader` | 0,0076 |
| `app/engine/forward_test.py::run_forward_test` | 0,0062 |
| `app/core/market_calendar.py::calendar_from_spec` | 0,0053 |
| `app/ml/backend/mixin.py::MLBackendMixin` | 0,0051 |

Aucune valeur au-dessus de 0,02 : **il n'y a pas de goulet d'étranglement
architectural unique**. Le graphe est plat et redondant — plutôt sain pour la
maintenance, mais l'analyse d'impact y est moins tranchée.

## 4. Le delta a amélioré la structure

Deux découpages livrés dans le delta :

- `app/core/risk_*.py` (8 modules, ~1 440 lignes) → paquet `app/core/risk/`
  (`curve`, `diagnostics`, `envelope`, `gate`, `ledger`, `notifier`, `sizer`,
  `state`) ;
- `app/core/smc_*.py` (7 modules, ~1 950 lignes) → paquet `app/core/smc/`
  (`geometry`, `primitives`, `quality`, `sessions`, `state`, `structure`,
  `volume`).

Même traitement côté frontend : `frontend/src/types/index.ts` passe de 1 112 à
16 lignes, éclaté en `generated.ts` (477, contrats API générés), `views.ts`
(578) et `ui.ts` (199).

Gain net de lisibilité. Il laisse une dette : `ARCH-01`.

---

## Constats

### ARCH-01 — Double identité de module pour tout le risque et tout le SMC (P2, CONFIRMÉ)

**Fichiers** : `app/core/risk_gate.py:1`, `app/core/_compat.py:16`, et les
14 autres shims.

Chaque module historique est devenu un shim de 5 à 12 lignes :

```python
from app.core.risk.gate import *          # noqa: F401,F403
from app.core.risk.gate import RiskGate as RiskGate
copy_privates('app.core.risk.gate', globals())
```

`copy_privates` recopie les noms privés dans le namespace du shim **au moment de
l'import**. Il en résulte deux objets-module distincts exposant les mêmes
symboles.

Répartition mesurée des sites d'import :

| Chemin | Sites |
|---|---:|
| `from app.core.risk_*` (shim) | 68 |
| `from app.core.risk.*` (paquet) | 35 |

**Scénario d'échec** — un test, ou un futur correctif, fait
`monkeypatch.setattr("app.core.risk_gate._default_venue_capital", f)`. Le patch
s'applique à la copie du shim ; tout le code de production qui importe
`app.core.risk.gate` continue d'appeler l'original. Le patch est silencieusement
sans effet et le test passe pour une mauvaise raison.

**Vérification** — lecture de `_compat.py` et comptage des imports. Deux
mesures montrent que le défaut **n'est pas actif aujourd'hui** :

- `grep -rn "^\s*global " app/core/risk/ app/core/smc/` ne renvoie rien : aucun
  rebinding de global. Le seul état module est
  `app/core/risk/diagnostics.py:24 _DEFAULT_STOP_PCT_REFERENCE = 0.025`, un
  flottant immuable ;
- aucun `monkeypatch.setattr` sur un shim dans `tests/`.

C'est donc une **dette latente**, pas un bug. Elle devient un bug le jour où
quelqu'un ajoute un cache au niveau module, ou un patch de test.

**Correctif proposé** — migrer les 68 sites vers le paquet, supprimer les
15 shims et `app/core/_compat.py`. Remplacement mécanique.

**Effort** : 2 h.

**Délégation IA** —
> Dans `app/` et `tests/`, remplacer tout import `app.core.risk_<m>` par
> `app.core.risk.<m>` et `app.core.smc_<m>` par `app.core.smc.<m>`
> (m parmi curve, diagnostics, envelope, gate, ledger, notifier, sizer, state ;
> et geometry, primitives, quality, sessions, state, structure, volume).
> Attention : `app/core/smc.py` existe et n'est **pas** concerné. Supprimer
> ensuite les 15 shims et `app/core/_compat.py`.
> Critère d'acceptation : `pytest -q` reste à 2 142 passés, et
> `grep -rn "app\.core\.risk_\|app\.core\.smc_" app/ tests/` ne renvoie rien.

---

### ARCH-02 — Deux composants frontend dépassent 680 lignes (P3, CONFIRMÉ)

**Fichiers** : `frontend/src/components/views/smart-replay-view.tsx:1`
(744 lignes, sortance 237) ;
`frontend/src/components/views/backtest-results.tsx:1` (681 lignes).

`backtest-results.tsx` a été retravaillé dans le delta (+179/−165) sans être
découpé. `smart-replay-view.tsx` est le 3ᵉ hub du dépôt, tous langages
confondus.

**Scénario d'échec** — pas de sortie fausse : c'est un coût de revue et un
risque de régression à chaque retouche. Le delta a d'ailleurs dû corriger
`b555674 fix(ui): hooks avant early return dans backtest-results`, une classe
de bug caractéristique des composants trop longs.

**Vérification** — `wc -l` et sortance dans le graphe.

**Effort** : 4 h par composant.

**Délégation IA** —
> Découper `smart-replay-view.tsx` : extraire la logique d'état vers des hooks
> `use-smart-replay-*.ts`, les blocs de rendu vers `components/cards/`, sur le
> modèle déjà appliqué à `use-smart-graph-chart.ts`. Aucune modification de
> comportement : `vitest run` et `frontend/e2e/tests/visual.spec.ts` doivent
> rester verts.

---

### ARCH-03 — `build_features` a une sortance de 354 (P3, PLAUSIBLE)

**Fichier** : `app/ml/backend/features.py:1`.

Premier hub du dépôt : 354 arêtes sortantes pour 12 entrantes. La fonction
appelle une grande partie de `app/core` (indicateurs, SMC, dérivés).

**Scénario d'échec** — une modification d'un indicateur de `app/core` change
silencieusement le vecteur de features, donc les modèles entraînés, sans
qu'aucun test ne relie les deux.

**Vérification** — lecture du graphe seule. **Non reproduit** : je n'ai pas
construit de cas où un changement d'indicateur casse un modèle.

**Correctif proposé** — figer un test de contrat sur la liste et l'ordre des
colonnes produites par `build_features`.

**Effort** : 1 h.

**Délégation IA** —
> Ajouter `tests/test_features_contract.py` : appeler `build_features` sur un
> OHLCV déterministe, vérifier la liste exacte des colonnes produites et leur
> ordre. Le test doit échouer si une feature est ajoutée, retirée ou renommée.
