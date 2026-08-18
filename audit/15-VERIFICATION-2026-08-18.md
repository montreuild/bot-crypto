# Vérification indépendante des correctifs — 18 août 2026

> **Objet.** Les PR #232 à #236 marquent une quarantaine de constats comme
> résolus, et les rapports ont été annotés en conséquence
> (cf. [`14-REVISION-2026-08-18.md`](14-REVISION-2026-08-18.md)). Ce document
> ne réécrit rien : il **contrôle dans le code que chaque correctif annoncé
> l'est effectivement**.
>
> Base vérifiée : **`d379abe`** (`main`, PR #236). Méthode : lecture du code
> aux emplacements cités, sans se fier aux messages de commit ni aux
> annotations des rapports.

---

## 1. Verdict

**Sur les 16 correctifs de fond contrôlés, 14 sont confirmés, 2 sont partiels.**
Aucun ne s'est révélé être une annotation sans code derrière. C'est un taux de
tenue inhabituel pour un lot de cette taille (5 PR, ~40 constats).

| Constat | Annoncé | Vérifié dans le code | Verdict |
|---|---|---|---|
| **F-01** frais d'entrée dans le PnL | ✅ | `backtest.py:844` `"pnl": round(pnl + realized - entry_fees, 6)` ; `total_pnl == net_profit` ; ancienne valeur conservée en `total_pnl_hors_frais_entree` | ✅ **confirmé** |
| **F-02** Sharpe `None` sous 10 obs | ✅ | `backtest.py:245,252` + `to_dict:579` propage `None` ; `_group_metrics:541-542` idem par stratégie | ⚠️ **partiel** — cf. R-01 |
| **F-03** `max_dd_p95` = pire cas | ✅ | `monte_carlo.py:84` `np.percentile(max_dds, 5)` | ✅ **confirmé** |
| **F-04** pas d'emprunt à levier ≤ 1 | ✅ | `execution.py:29-47` paramètres `max_leverage` / `own_funds`, `borrowed = max(0, notional − own_funds)` | ✅ **confirmé** |
| **F-05** plafond notionnel venue | ✅ | `risk_ledger.py:94-98` `venue_max_notional`, motif `enveloppe_venue` | ✅ **confirmé** |
| **F-06** drawdown mark-to-market | ✅ | `backtest.py:178,225` série `equity_mtm`, `_mark_mtm()` appelée par barre | ✅ **confirmé** |
| **B-01** fill au gap | ✅ | `backtest.py:955-970` `_fill_at_level()` retourne `(exec_price, ref, gapped)` ; `ctx.open_arr` extrait | ✅ **confirmé** |
| **B-02** multi-positions | ✅ | `backtest.py` : `positions` est un dict, boucle `for _pk, _pos in list(positions.items())`, **et la recherche de signal continue en position** (plus de `continue`) | ⚠️ **partiel** — cf. R-02 |
| **B-05** `min_notional` après quantification | ✅ | `backtest.py:1373-1377` contrôle sur la taille finale | ✅ **confirmé** |
| **L-01** stop sur bougie en formation | ✅ | `position_manage_mixin.py:86-93` `probe = lo if long else hi` via `get_forming_range` | ✅ **confirmé** |
| **L-05** garde de réservation | ✅ | `risk_ledger.py:76-77` `if pos_key in self._positions: return Decision(False, "deja_reserve", …)` | ✅ **confirmé** |
| **L-06** plafond caché 25 % | ✅ | `balance_sync.py` : **plus aucune occurrence** de `0.25` | ✅ **confirmé** |
| **A-01** clôture atomique | ✅ | `position_close_mixin.py:329-336` une seule `session_scope`, trois appels en `commit=False`, un commit final | ✅ **confirmé** |
| **M-06** `overlap` invalide le résultat | ✅ | `backtest.py:122` `"overlap_warning": True, "invalidated": True` | ✅ **confirmé** |
| **N-01** holdout dans le dimensionnement | ✅ | `optimizer_search.py:151` `oos_needed / (_OOS_FRACTION * (1.0 - _HOLDOUT))` | ✅ **confirmé** |
| **N-02** apply manuel sur holdout | ✅ | `routes/optimizer.py:342-347` `_h = job.get("holdout")`, `_gate_source` exposé | ✅ **confirmé** |
| **N-04** `exit_mode` en live | ✅ | `position_open_mixin.py:498-503` `apply_exit_mode` appelé avec la même résolution de priorité qu'en backtest | ✅ **confirmé** |

Contrôle de non-régression sur la propagation de `None` : `composite_score`
lit `res.get("sharpe") or 0` (`opt_scoring.py:59,68`) et `beats_baseline`
garde explicitement `oos_sharpe is not None` (`opt_scoring.py:215-217`). Côté
frontend, `const num = z.number().nullish()` (`schemas.ts:35`) accepte `null`,
et les trois sites d'affichage du Sharpe issu d'un backtest sont protégés
(`lab/page.tsx:1274` `?.toFixed ?? '—'`, `strategy-comparison-table` `value ==
null ? '—'`, `cost-simulator-panel:229`). **Pas de régression d'affichage.**

Tests : 1 723 → **1 914** (+191 depuis l'audit initial).

---

## 2. Les deux réserves

### R-01 🟠 F-02 n'a pas été porté côté live — et le code y déclare l'invariant

`app/live/health_mixin.py:335-367` calcule le Sharpe par stratégie du live.
Il porte ce commentaire :

> S4-01 : Sharpe aligné sur `BacktestResult._compute_metrics()`
> (`engine/backtest.py`) […] Les deux Sharpe (live/backtest) **doivent rester
> comparables** : c'est pourquoi la correction d'annualisation qui touche le
> backtest **DOIT toucher celui-ci en même temps**.

Or le code juste en dessous n'a pas bougé :

```python
if len(pnls) >= 3 and initial_capital > 0:      # ← plancher à 3, pas 10
    ...
    d["sharpe"] = round(_safe_float(raw, 0.0), 3)
else:
    d["sharpe"] = 0.0                            # ← 0.0, pas None
```

État après correctif :

| Chemin | Plancher | Valeur si insuffisant |
|---|---|---|
| Backtest (`BacktestResult`) | **10 observations** | `None` |
| Backtest (`_group_metrics`, par stratégie) | **10 observations** | `None` |
| **Live (`health_mixin`)** | **3 trades** | **`0.0`** |

Conséquences :

1. **L'invariant que le fichier énonce est violé** : les deux Sharpe ne sont
   plus comparables. Un slot à 5 trades affiche un Sharpe live chiffré et un
   Sharpe backtest `—`.
2. `study-vs-live-card.tsx` compare précisément ces deux grandeurs.
3. Le Sharpe live sur 3 trades reste exactement le défaut mesuré par l'audit
   initial (104/158 runs à |Sharpe| > 10) — il a simplement changé de côté.

**Correction** : importer `MIN_SIGNIFICANT_TRADES` dans `health_mixin` et
appliquer le même plancher, avec `None`. Puis protéger
`portfolio/page.tsx:245` (`{stats.sharpe.toFixed(2)}`, sans garde) — il ne
plante pas aujourd'hui **uniquement parce que** `health_mixin` renvoie encore
un flottant. Corriger le backend sans corriger cette ligne la ferait planter.

C'est exactement le mode d'erreur que je notais dans ma remarque de méthode du
17 août : vérifier l'invariant là où le code l'énonce, pas chez chacun de ses
consommateurs. Il s'est reproduit ici, dans le fichier qui **écrit**
l'invariant.

### R-02 🟡 B-02 : le multi-positions est réel, le `RiskLedger` n'est toujours pas branché

La partie lourde de B-02 est faite et bien faite : `positions` est un
dictionnaire, la gestion itère dessus, et surtout la recherche de signal n'est
plus court-circuitée quand une position est ouverte — c'était le cœur du
constat.

Reste le second volet de ma recommandation (« brancher le **vrai**
`RiskLedger`, il est déjà sans I/O et thread-safe »). Le backtest **réimplémente
les plafonds en ligne** ; le commentaire `backtest.py:1374` le dit :
« … comme `RiskLedger.reserve` côté live ».

Donc `RiskLedger.reserve` — la fonction qui arbitre réellement en production,
et dont L-05 vient de montrer qu'elle pouvait fuir — n'est **toujours exercée
par aucun backtest**. Deux implémentations d'une même règle, dont une seule est
testée en conditions réalistes.

Ce n'est plus critique (le multi-positions supprimait le gros du risque), mais
le constat X-04 « deux implémentations pour un même concept » s'applique
désormais aux plafonds de risque.

---

## 3. Ce qui reste ouvert et ne l'a pas été traité

Vérifié sur `d379abe` :

| Réf | État |
|---|---|
| **B-03** `walk_forward.run()` sans `timeframe` | ouvert — `def run(self, df, symbol)` inchangé |
| **B-04** walk-forward sans réoptimisation par fold | annoncé « stabilité » ; le renommage clarifie, la réoptimisation reste absente |
| **M-07** modèle final entraîné sur IS+OOS par défaut | ouvert |
| **U-01 → U-12** frontend | `frontend/` n'a reçu que des ajouts d'affichage ; i18n toujours à zéro consommateur, 98 composants clients, sondage inchangé |
| **X-02** duplication `scoring_statistique_opus` v4/v5 | ouvert |
| **D-07** aucun trade live dans `oos_tracker` | `data/` a été purgé avant merge — le compteur repart de zéro, la question reste entière |

---

## 4. Le point qui n'a pas bougé

`8edf54a chore(data): purge oos_tracker et backtest_history pre-merge` est la
bonne décision : les données produites par l'ancien calcul de PnL, de Sharpe et
de Monte-Carlo n'étaient plus comparables aux nouvelles, et les conserver aurait
mélangé deux conventions dans la même interface. `schema_version` +
`git_commit` sont posés (D-06 traité).

Mais la conséquence est que **le dépôt n'a plus aucune mesure**. Les correctifs
de cette semaine sont solides sur le plan du raisonnement et vérifiés sur le
plan du code ; ils n'ont encore produit **aucun chiffre**.

Le point aveugle central de l'audit initial est donc intact, et il est même
redevenu plus visible : tant que le bot ne tourne pas assez longtemps pour que
`oos_tracker` rende ses premiers verdicts (`live.n_trades > 0`), on ne saura pas
si un backtest désormais juste décrit ce que fait le live. **C'est la seule
tâche restante qui ne peut pas être accélérée par du code.**
