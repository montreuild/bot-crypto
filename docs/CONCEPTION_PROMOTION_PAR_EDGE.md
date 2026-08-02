# Conception — Promotion d'un bot par la significativité de l'edge

> Statut : spec validée (conversation produit, 2026-06). Remplace le critère
> d'activation « ≥ 10 trades **live** » par une **preuve d'edge sur le backtest**
> (borne basse du cône Monte-Carlo > 0), le live ne servant plus qu'à confirmer la
> **fidélité d'exécution**. Réversible, opt-in via config, avec **bypass manuel**.
>
> À lire avec : `docs/SYNTHESE_VISION_PRODUIT.md` (vision) et
> `docs/ANALYSE_CYCLE_DE_VIE_STRATEGIES.md` (état des lieux).

---

## 1. Problème

La machine à états actuelle (`app/live/slot_lifecycle.py`) exige
`active_min_trades` trades **live** (défaut 10) pour passer **Actif**. Une
stratégie robuste mais **basse fréquence** (ex. 3 trades/an) :

- met **des années** à accumuler 10 trades live → coincée en *Essai* ;
- a un cône Monte-Carlo vide sur la fenêtre glissante 30–60 j → verdict
  « pas assez de trades réels » permanent ;
- garde un budget jamais récompensé (rééquilibrage perf exige 3 trades/7 j) ;
- immobilise du capital qui dort (coût d'opportunité).

Le système confond deux questions distinctes :

1. **L'edge est-il réel ?** — question **statistique**, répondable *maintenant*
   sur tout l'historique backtest. La taille d'échantillon pertinente est le
   **nombre de trades**, pas le nombre de bougies (40 000 bougies 1h à 3
   trades/an ≈ 14 trades : 14 points de preuve, pas 40 000).
2. **Le live colle-t-il à la simulation ?** — question d'**exécution**
   (slippage, fills). Elle a besoin de quelques fills, pas de 10 trades pour
   re-prouver l'edge.

## 2. Principe retenu

Découpler **edge** (jugée sur le backtest) et **fidélité** (confirmée par le
live), avec un **droit de veto manuel** de l'utilisateur.

```
 CANDIDAT ──(edge prouvée sur backtest)──► ESSAI ──(fidélité live confirmée)──► ACTIF
    ▲                                          │                                  │
    │                                     (edge décayée)                    (fidélité cassée /
    │                                          │                             budget effondré)
    └──────────────── re-optimisation ◄──── RETIRÉ ◄───────────────────────────┘

 Bypass : l'utilisateur peut FORCER un bot en ACTIF à tout moment (manual_active).
```

### 2.1 Critère d'edge (choix : borne basse du cône MC > 0)

Sur les trades **simulés** du forward-test glissant (taille `n_sim`), on
bootstrappe la moyenne du rendement par trade et on prend son intervalle de
confiance. L'edge est jugée **significative** si :

```
edge_significant ⟺  ci_low_pct > 0                        (borne basse > 0)
                AND  n_sim ≥ edge_min_trades              (plancher anti-dégénérescence)
                AND  worst_trade_pct ≥ −max_worst_trade_pct   (garde-fou de queue)
```

- **`ci_low_pct > 0`** : équivaut à un t-test unilatéral ~95 % de l'expectancy.
  La largeur du cône ~ `std/√n_sim` encode la taille d'échantillon : 3 trades →
  cône large → borne basse < 0 → non significatif ; 30 trades consistants →
  cône resserré → borne basse > 0 → significatif. **Sans attendre de live.**
- **`n_sim ≥ edge_min_trades`** (défaut 20) : le bootstrap *dégénère* à très
  petit n (à n=1 la variance est nulle et la borne basse = la valeur). Ce
  plancher porte sur les trades **backtest**, pas live — une stratégie de
  fréquence normale sur 20–40k bougies a des centaines de trades et le passe
  instantanément ; seules les ultra-basses fréquences sont concernées (et pour
  elles, 14 trades sont *réellement* insuffisants → bypass manuel).
- **`worst_trade_pct ≥ −max_worst_trade_pct`** (garde-fou de queue, défaut
  50 %) : **indispensable pour les 100 % winrate.** Le cône a une variance
  quasi nulle (gains réguliers) → t-stat qui explose → il passerait le gate…
  alors qu'il cache souvent une queue de perte non encore observée. On exige au
  minimum que la pire perte simulée reste bornée (donc qu'il y ait un stop). Le
  bootstrap ne voit **que** les pertes déjà observées : une queue jamais
  réalisée reste invisible — d'où ce garde-fou explicite.

### 2.2 Critère de fidélité (choix : proxy `in_band`, v1)

Le live ne re-prouve plus l'edge ; il confirme que l'exécution est fidèle :

```
fidelity_ok ⟺  live_trades ≥ fidelity_min_fills   (défaut 2)
            AND in_band is True                    (le réel ne diverge pas de la sim)
```

`in_band` est déjà calculé par `app/core/oos_tracker.py` (`_verdict`) : le
rendement réel moyen tombe dans la fourchette MC prédite pour ce nombre de
trades. **v1 = ce proxy** (zéro nouvelle plomberie sur le chemin de trading).

> **Évolution (Phase ultérieure) — fidélité par slippage.** Plus rigoureux :
> capturer dans `live_trader` le **prix de fill attendu** (sim) à chaque
> entrée/sortie, le comparer au fill réel, et exiger `|slippage| ≤ tol` sur les
> premiers fills. Demande un ajout côté boucle live ; non retenu en v1.

### 2.3 Retrait

Inchangé dans l'esprit, alimenté par les nouveaux signaux :

```
RETIRÉ ⟺  (live_trades ≥ 1 ET budget < plancher_budget_pct)          (budget effondré)
       OU (in_band is False ET ret_live < 0 ET live_trades ≥ eval_min) (edge décayée /
                                                                        fidélité cassée)
```

Lissage anti-flush conservé (plancher `min_active_bots`, quota
`max_demotions_per_day`, file de re-optimisation).

### 2.4 Forçage manuel

`lifecycle.force_active: [slot_key, …]` (`config/lifecycle.yaml`, persistant).
Un bot listé est **forcé ACTIF** quelle que soit la dérivation (sauf qu'il peut
toujours être désactivé via le toggle de slot). C'est le **droit de
veto/promotion** de l'utilisateur prévu par la vision. Posé/retiré via
`POST /api/bots/{slot_key}/force-active?enabled=true|false`.

> **D6 / S11.** La clé s'appelait `manual_active` et portait **15 slots** dans
> la config livrée ; elle est vide par défaut et l'ancien nom n'est plus lu que
> par rétro-compatibilité (WARNING de dépréciation).
>
> ⚠ **Portée réelle du forçage** — plus large que la seule promotion :
> `_propose` retourne `ACTIF` **avant** toute autre règle, donc un slot forcé
> échappe aussi aux deux règles de RETRAIT (budget effondré sous
> `plancher_budget_pct`, live qui contredit la simulation en perdant). Un bot
> forcé perdant n'est jamais retiré et n'entre jamais dans la file de
> ré-optimisation. Verrouillé par
> `tests/test_phase2_lifecycle_alloc.py::test_force_active_also_blocks_the_retrait`.
>
> ⚠ **Ce que le forçage ne fait PAS** : il ne fait pas trader un bot. La
> sélection des bots qui tournent vient du classement OOS
> (`optimizer_results` + `MIN_VIABLE_SCORE` + `trading.top_strategies_per_tf`,
> cf. `get_active_strategies_per_tf`). Le forçage agit sur l'**état** affiché
> et sur les transitions, pas sur la sélection.

## 3. Machine à états (`_propose`)

```
if force_active:                               return ACTIF   # court-circuite TOUT, retrait compris
if live_trades ≥ 1 and budget < plancher:      return RETIRE
if in_band is False and ret < 0
                     and live_trades ≥ eval_min: return RETIRE
if not (edge_significant):                     return CANDIDAT
if live_trades ≥ fidelity_min_fills
                     and in_band is True:      return ACTIF
                                               return ESSAI   # edge prouvée, fidélité en attente
```

## 4. Données & fichiers touchés

| Fichier | Changement |
|---|---|
| `app/core/oos_tracker.py` | `_edge_contract()` : bootstrap de la moyenne sur **`n_sim`** trades → `edge = {available, n, expectancy_pct, ci_low_pct, ci_high_pct, worst_trade_pct}` ajouté à l'enregistrement. |
| `app/live/slot_lifecycle.py` | nouveaux seuils config ; `_force_active` + `set_force_active()` (alias déprécié `set_manual_active`) ; `_propose()` réécrit (§3). |
| `app/live/live_trader.py` | `_lifecycle_thread` alimente `slots_data` avec `edge_ci_low`, `edge_n`, `worst_trade_pct`, `live_in_band`. |
| `app/api/routes/portfolio.py` | `/api/bots` expose `edge`, `edge_significant`, `force_active` (+ `manual_active` à l'identique le temps de la migration) ; endpoint `force-active`, qui écrit `force_active` et **supprime** `manual_active` du fichier — deux listes de forçage ne doivent pas cohabiter. |
| `config/lifecycle.yaml` | `lifecycle.edge_min_trades`, `edge_conf`, `max_worst_trade_pct`, `fidelity_min_fills`, `force_active`. |
| `frontend/src/app/bots/page.tsx` | frise/explication basées edge+fidélité ; ligne « Edge backtest » ; bouton « Forcer Actif » ; filtre via le helper `isForcedActive()`. |
| `tests/test_phase2_lifecycle_alloc.py` | tests `_propose` mis à la nouvelle sémantique + cas edge/queue/bypass. |

## 5. Paramètres par défaut

```yaml
lifecycle:
  edge_min_trades: 20         # plancher de trades BACKTEST pour juger l'edge
  edge_conf: 0.90             # niveau de confiance du cône (IC 90 %)
  max_worst_trade_pct: 50.0   # garde-fou de queue : pire trade simulé toléré
  fidelity_min_fills: 2       # fills live mini pour confirmer la fidélité
  force_active: []            # bots forcés ACTIF (droit de veto utilisateur)
```

## 6. Comportement attendu sur les cas limites

| Cas | Résultat |
|---|---|
| Stratégie fréquence normale, edge réelle (centaines de trades backtest) | **Actif en quelques fills** (plus d'attente de 10 trades live). |
| Basse fréquence, 15 trades backtest, edge réelle | Reste **Candidat** (n < `edge_min_trades`) → **forçage manuel** si l'utilisateur est convaincu. |
| 100 % winrate, 3 trades | **Candidat** (n trop petit + cône large). |
| 100 % winrate, sans stop / queue énorme | **Candidat** bloqué par le garde-fou de queue, même si le cône est étroit. |
| Edge prouvée mais live diverge fort (`in_band False`, perte, n≥eval_min) | **Retiré** → re-optimisation. |

## 7. Règle d'or

L'edge se juge sur le **backtest** (tout l'historique disponible), pas en
ré-accumulant des trades live. Le live ne sert qu'à confirmer la **fidélité**.
Le **garde-fou de queue** n'est jamais optionnel : un cône étroit ne prouve pas
l'absence de risque extrême. Le **bypass manuel** reste la soupape pour les cas
que la statistique ne peut pas trancher (échantillon trop court).
