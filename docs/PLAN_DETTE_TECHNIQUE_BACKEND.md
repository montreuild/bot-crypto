# Plan de traitement — 324 suggestions du code-review-graph

> Source : `refactor_tool(mode="suggest")` sur le graphe reconstruit au SHA
> `ea3f91d` (462 fichiers, 4 664 nœuds, 45 329 arêtes, 15 communautés).
> Triage automatisé rejouable : `scripts/triage_refactor.py` (cf. §6).

---

## 1. Ce que dit l'outil, et ce qu'il dit vraiment

L'outil remonte **324 suggestions** réparties en deux types :

| Type | Nombre | Ce que l'outil affirme |
|---|---|---|
| `remove` | 190 | « Symbole non référencé : aucun appelant, aucun test, aucun importeur, pas un point d'entrée » |
| `move` | 134 | « Symbole dans la communauté A mais appelé uniquement depuis la communauté B » |

**Première correction : ces suggestions ne sont pas toutes backend.**

| Périmètre | `remove` | `move` | Total |
|---|---|---|---|
| Backend (`app/`, `optimize_runner.py`) | 102 | 134 | **236** |
| Frontend (`frontend/src`, `next.config.mjs`) | 88 | 0 | **88** |

**Deuxième correction, la plus importante : ~80 % des `remove` sont des faux
positifs.** Le triage automatisé sur les 190 suggestions donne :

| Cause probable | Nombre | % |
|---|---|---|
| Référence réelle non vue par le graphe | 58 | 31 % |
| Re-export via `__init__.py` | 42 | 22 % |
| **Aucune référence — candidat réel** | **38** | **20 %** |
| Appel entre mixins (`self.x()`) | 35 | 18 % |
| Passé en callback (`f(x)`, `cb=x`) | 15 | 8 % |
| Décorateur framework | 2 | 1 % |

### Pourquoi le graphe se trompe ici

Le parsing est statique (tree-sitter) ; quatre motifs très présents dans ce
codebase lui échappent :

1. **Composition par mixins.** `LiveTrader` agrège `PositionOpenMixin`,
   `PositionCloseMixin`, `HealthMixin`, `AutoOptMixin`… Un `self._maybe_lifecycle()`
   appelé depuis `live_trader.py` vers `auto_opt_mixin.py` n'est pas résolu.
   *Exemple :* `AutoOptMixin._maybe_lifecycle` est signalé mort alors que
   `live_trader.py:268` l'appelle.
2. **Callbacks passés par valeur.** `on_apply_callback=_on_apply` dans
   `routes/optimizer.py:159`, `_save_yaml(_apply)` dans `routes/portfolio.py:333`.
3. **Re-exports.** `app/ml/backend/__init__.py` ré-exporte `exit_td_window_active` ;
   `app/core/indicators.py` ré-exporte `bearish_excess_series`.
4. **Points d'entrée framework.** `@router.websocket("/ws")` sur
   `websocket_endpoint` — signalé mort alors que c'est le WebSocket live du bot,
   vérifié fonctionnel (`[WS] connected` en console).

Cas le plus parlant : **`_locked` (`app/core/risk_state.py`) est signalé comme
non utilisé alors qu'il compte 54 références** et est importé nommément par
`app/core/risk_gate.py:30`. Supprimer aveuglément la liste casserait le bot.

> **Règle de conduite : aucune suppression sans double vérification.** Le lot 1
> ci-dessous ne se déclenche que sur une suite de tests verte
> (`pytest -m "not slow"` → 1392 passés, 3 ignorés).

---

## 2. Les 134 `move` : signal faible, action différée

Après reclassement des 134 suggestions `move` :

| Nature réelle | Nombre | Verdict |
|---|---|---|
| Appelé uniquement par les tests | 58 | Signal exploitable — voir lot 3 |
| Couplage inter-modules | 48 | Bruit : c'est l'architecture voulue |
| Symbole décoré (framework, cache, retry) | 25 | Faux positif |
| Handler FastAPI / dépendance `Depends()` | 3 | Faux positif |

**Le conseil littéral de l'outil — « déplacer vers la communauté `tests-fetch` »
— n'a pas de sens** : `tests-fetch` est la communauté des tests, on ne déplace
pas du code de production dedans. 85 des 134 `move` pointent vers elle.

Ce que le signal veut vraiment dire, c'est : *ce symbole de production n'est
exercé que par les tests*. Deux lectures possibles, opposées :
- soit c'est du code mort maintenu en vie par son test (→ supprimer les deux) ;
- soit c'est un câblage de production manquant (→ bug latent).

Exemples relevant clairement de la **seconde** lecture, donc à ne pas toucher :
`verify_api_key` (`app/api/helpers.py`) est utilisé partout via
`Depends(verify_api_key)` ; `publish_trade_opened`, `publish_trade_closed`,
`publish_signal`, `publish_risk_event` (`app/core/events.py`) sont le bus
d'événements qui alimente le WebSocket.

Les 48 « couplage inter-modules » recoupent les avertissements de
`get_architecture_overview` (625 arêtes `core-fetch` ↔ `tests-fetch`, 304
`live-position` ↔ `tests-fetch`…). Ces chiffres mesurent surtout **le volume de
tests**, pas un défaut de conception. À ignorer en tant qu'action.

---

## 3. Lots de travail

### Lot 1 — Code mort confirmé (38 candidats, dont 18 backend)

Les seuls symboles à **zéro référence** dans tout le corpus (439 fichiers :
`app/`, `tests/`, `scripts/`, `strategies/`, `recipes/`, `frontend/src`,
`frontend/e2e`, `cli.py`, `optimize_runner.py`, `config.yaml`).

**Backend (18)** — à instruire un par un :

| Fichier | Symboles | Note |
|---|---|---|
| `app/core/correlation_matrix.py` | `correlation_malus`, `detect_redundant_bots`, `diversification_ratio` | Module entier jamais appelé — vérifier s'il s'agit d'une fonctionnalité prévue non câblée |
| `app/engine/regime_stress_test.py` | `regime_summary`, `stress_test_by_regime` | Idem |
| `app/ml/overfitting_gate.py` | `compute_auc_oos`, `validate_model_quality` | ⚠ Un « gate anti-surapprentissage » non appelé est un **risque produit**, pas du code mort à supprimer |
| `app/live/live_trader.py` | `_get_cached_atr`, `persist_allocator_state` | ⚠ `persist_allocator_state` : vérifier que la persistance passe bien par un autre chemin |
| `app/engine/opt_scoring.py` | `deflated_sharpe_ratio` | Bailey & LdP — `core/deflated_sharpe.py` retiré (X-01) |
| `app/core/performance_metrics.py` | `compute_extended_metrics` | |
| `app/core/exchange.py` | `RobustExchange.fetch_margin_balance_usdc` | ⚠ Marge : vérifier avant de toucher |
| `app/core/events.py` | `publish_ticker` | |
| `app/core/feature_store.py` | `_ohlcv_hash_expr` | Helper privé |
| `app/ml/model_versioning.py` | `migration_check` | |
| `app/ml/recipe.py` | `Recipe.window_for` | |
| `app/ml/backend/__init__.py` | `MLBackend.get_bt_features` | |
| `app/api/routes/ws.py` | `websocket_endpoint` | ❌ **Faux positif** : handler `@router.websocket("/ws")`, entrée live du bot |

**Ce tableau contient déjà un faux positif identifié** (`websocket_endpoint`),
ce qui donne la mesure du résidu attendu : compter ~1 sur 15 même dans la liste
« propre ».

Trois d'entre eux ne relèvent pas du nettoyage mais de l'**investigation
produit** : un gate anti-surapprentissage (`validate_model_quality`), une
persistance d'allocateur (`persist_allocator_state`) et un calcul de Sharpe
dégonflé (`is_deflated_sharpe_significant`) qui ne sont jamais appelés sont plus
probablement des câblages oubliés que du code superflu. **À trancher avant toute
suppression** — c'est le point le plus important de ce plan.

**Frontend (20)** — hors périmètre de ce document, à traiter avec le chantier de
typage des réponses d'API (cf. `audit-ui-ux-bot-crypto.md`).

*Effort : 1 à 2 jours, dont l'essentiel en investigation, pas en suppression.*

### Lot 2 — Durcir la détection (préalable à tout le reste)

Sans ceci, chaque nouvelle exécution du graphe reproduira 80 % de bruit.

1. Déclarer les points d'entrée framework au graphe (handlers FastAPI,
   `Depends()`, `@app.websocket`) pour qu'ils cessent d'être « morts ».
2. Croiser avec un outil qui comprend Python dynamiquement — `vulture` avec un
   seuil de confiance élevé, ou `ruff` (`F401` imports inutilisés, déjà dans le
   projet via `ruff.toml`) — et ne retenir que l'**intersection** des deux
   sources.
3. Versionner `scripts/triage_refactor.py` (cf. §6) pour rejouer le triage à
   chaque reconstruction du graphe.

*Effort : 0,5 à 1 jour. À faire en premier.*

### Lot 3 — Symboles exercés uniquement par les tests (58)

Pour chacun, une seule question : **est-ce un câblage manquant ou du code mort ?**

Procédure : partir des 58, exclure les `Depends()` / handlers / publishers
d'événements (faux positifs établis), puis pour le reste vérifier si le chemin
de production existe. Un symbole testé mais jamais appelé en production est le
symptôme classique d'une fonctionnalité développée puis jamais branchée.

*Effort : 2 à 3 jours. À faire après le lot 2, qui réduira la liste.*

### Lot 4 — Couplage inter-communautés (48) : ne rien faire

Aucune action recommandée. Les avertissements « High coupling » du graphe
mesurent le volume de tests, pas un défaut d'architecture. À réévaluer seulement
si une refonte modulaire du backend est décidée par ailleurs.

---

## 4. Ce qu'il ne faut PAS faire

- **Appliquer `apply_refactor_tool` en masse.** Sur 190 `remove`, ~152 casseraient
  ou dégraderaient le code.
- **Suivre les `move` littéralement.** Déplacer du code de production dans la
  communauté des tests n'a pas de sens.
- **Traiter ce plan avant la dette frontend.** Le backend est vert
  (pytest `not slow`) ; le typage des réponses API est généré
  (`app/api/schemas.py` → `frontend/src/types/generated.ts`, FE-03, PR #256).
  `index.ts` ne garde que les vues riches (BacktestResult, OptimizeJob, ML).

---

## 5. Ordre recommandé

| Rang | Lot | Effort | Pourquoi ce rang |
|---|---|---|---|
| 1 | Lot 2 — durcir la détection | 0,5-1 j | Sans ça, tout le reste se fait à 80 % de bruit |
| 2 | Lot 1 — les 3 cas « câblage oublié ? » | 0,5 j | Risque produit potentiel (gate ML, persistance, Sharpe dégonflé) |
| 3 | Lot 1 — reste du code mort backend | 1 j | Nettoyage à faible valeur mais peu risqué, tests verts |
| 4 | Lot 3 — testés mais jamais appelés | 2-3 j | Après réduction par le lot 2 |
| 5 | Lot 4 — couplage | — | Sans objet |

**Total : 4 à 5 jours**, dont environ un tiers d'investigation produit et deux
tiers de nettoyage. À arbitrer contre le chantier de typage des réponses d'API,
qui a une valeur défensive supérieure : c'est lui qui aurait évité les crashs de
`/ml` et du drawer `/bots`.

---

## 6. Reproduire le triage

Le script de triage est versionné dans `scripts/triage_refactor.py`. Il prend en
entrée l'export JSON de `refactor_tool(mode="suggest")` et produit le classement
par cause probable du §1.

```bash
python scripts/triage_refactor.py . suggestions.json triage.json
```

Chiffres de référence (SHA `ea3f91d`) : 439 fichiers scannés, 190 suggestions
`remove` analysées, 38 candidats réels (20 %).
