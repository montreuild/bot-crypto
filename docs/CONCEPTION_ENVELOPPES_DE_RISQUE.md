# Enveloppes de risque — conception cible

> Statut : **spécification à implémenter en une passe**. Pas d'étape
> intermédiaire, pas de drapeau de bascule, pas de rétro-compatibilité sur les
> clés supprimées. Le code mort listé au §8 est supprimé dans la même passe.

---

## 1. Le problème que cette cible résout

Quatre défauts constatés, tous issus de la même racine — **le capital est
découpé en N parts égales pour N paris qui ne sont pas indépendants** :

1. **Deux bases pour une décision.** `compute_size` dimensionne sur l'équité
   globale, `can_allocate` plafonne sur le budget du slot. Résultat mesuré sur
   la config livrée : notionnel 200 € contre un plafond de 95 € → **refus
   systématique** pour tout stop inférieur à ~10 %.
2. **Diversification fictive.** 10 slots actifs sur 11 sont sur `BTC/USDC`
   (`breakout` en 1h/4h/1d, `scoring_statistique_opus` en 15m/30m…). Ce ne sont
   pas 11 risques, c'est un pari BTC exprimé dix fois. Diviser le capital par 11
   ne réduit pas le risque, il fragmente l'exécution.
3. **Capacité d'exécution décorrélée du nombre de slots.** `max_pyramiding: 2`
   n'autorise que 2 positions BTC simultanées : les 8 autres bots sont refusés
   au premier arrivé. Les budgets, eux, restent divisés par 11.
4. **Base économique divergente backtest ↔ live.** Le backtest mesure un bot sur
   1 000 €, le live le dimensionne sur ~90 €, et **le rapport d'edge ne le dit
   pas**. L'expectancy simulée et l'expectancy live ne sont pas comparables,
   alors que la promotion par edge repose sur leur comparaison.

Principe directeur de la cible :

> **Le risque se budgète du haut vers le bas, en devise. La taille se calcule du
> bas vers le haut, par la distance au stop. Une décision, une seule base.**

---

## 2. Le modèle

Trois enveloppes emboîtées. Chacune porte **deux plafonds distincts** :

| Grandeur | Unité | Ce qu'elle contrôle |
|---|---|---|
| **enveloppe** (`envelope`) | devise | le notionnel — l'immobilisation de capital |
| **budget de risque** (`risk_budget`) | devise | la perte maximale si tous les stops sautent |

```
VENUE  okx-margin
       envelope     = capital                      = 1 000 €
       risk_budget  = capital × venue_risk_pct     =    30 €
  │
  ├─ SYMBOLE  BTC/USDC
  │           envelope    = venue.envelope × max_symbol_exposure_pct = 1 000 €
  │           risk_budget = envelope × symbol_risk_pct               =    20 €
  │     │
  │     ├─ SLOT  breakout::1h::BTC/USDC
  │     │        envelope    = symbole.envelope × poids(confiance edge)
  │     │        risk_amount = envelope × trade_risk_pct
  │     │   │
  │     │   └─ TRADE  size     = risk_amount / stop_dist
  │     │              notional = size × prix   ≤ slot.envelope × levier
```

### 2.1 Les deux plafonds ne se remplacent pas

- Le **budget de risque** borne la perte. Il est consommé par
  `risque_engagé = |entrée − stop_courant| × taille` pour chaque position
  ouverte, et **libéré à la clôture**.
- L'**enveloppe** borne l'exposition. Elle est consommée par le notionnel.

Conséquence voulue : quand le trailing remonte le stop au point mort, le risque
engagé **diminue** et libère du budget pour un autre bot. L'enveloppe, elle,
reste consommée tant que la position est ouverte.

### 2.2 Ce que le budget de risque rend inutile

| Garde-fou actuel | Remplacé par | Pourquoi |
|---|---|---|
| `max_pyramiding` | budget de risque **symbole** | on ouvre autant de positions que le budget en supporte ; compter les positions était un proxy grossier de ce plafond |
| `max_positions` | budget de risque **venue** | idem au niveau du livre |
| corrélation directionnelle ≥ 75 % même sens | budgets symbole + venue | n bots longs sur BTC ne peuvent pas perdre plus que le budget BTC, quel que soit leur nombre |
| `max_notional_pct` (20 % global) | `slot.envelope × levier` | plafond exprimé sur la bonne base |
| `per_bot_sizing` (drapeau) | — | il n'existe qu'**une** base : l'enveloppe du slot |

La corrélation **entre symboles** n'est pas couverte par les budgets symbole :
c'est le rôle du `venue_risk_pct`, qui doit être réglé **strictement inférieur à
la somme des budgets symbole** (voir §7.2).

### 2.3 Poids des slots — par confiance d'edge

Les poids se répartissent **à l'intérieur d'un symbole** (les bots d'un même
symbole sont des quasi-substituts, pas des risques additionnels) :

```
poids(slot) = max(edge_ci_low, 0) / Σ max(edge_ci_low, 0)   sur les slots du symbole
```

- `edge_ci_low` = borne basse de l'IC d'expectancy (`oos_tracker._edge_contract`).
  On pondère par la **confiance**, jamais par la performance ponctuelle.
- Aucun slot du symbole n'a d'edge mesurée → répartition **égale**.
- Un slot à `edge_ci_low ≤ 0` reçoit un poids nul : il ne trade pas tant que son
  edge n'est pas prouvée.
- Plancher : `min_slot_weight` (défaut 0.05) pour éviter des enveloppes trop
  petites pour franchir `min_notional`. Les poids sont renormalisés après
  application du plancher.

### 2.4 Capacité d'exécution, pas nombre de slots déclarés

Le dénominateur de la répartition n'est plus « N stratégies déclarées » : les
slots à poids nul (edge non prouvée, slot désactivé, coupe-circuit actif) sont
**exclus du dénominateur**. L'enveloppe se répartit entre les slots réellement
capables de trader.

---

## 3. Configuration cible

### `config/risk.yaml` — nouveau bloc `risk.envelopes`

```yaml
risk:
  # Taux de risque par trade, en % de l'enveloppe du SLOT.
  profile: normal              # prudent 0.01 | normal 0.025 | agressif 0.05
  profiles: {prudent: 0.01, normal: 0.025, agressif: 0.05}
  min_slot_weight: 0.05
  base_drift_tolerance: 0.20   # au-delà, l'edge est réputée périmée (§5.2)

  envelopes:
    okx-margin:                       # clé = nom de venue (venues.defs)
      capital: 1000                   # devise de la venue (quote_currency)
      max_symbol_exposure_pct: 1.00   # 100 % : un seul symbole tradé (BTC)
      symbol_risk_pct: 0.02           # 20 € de perte max sur BTC
      venue_risk_pct: 0.03            # 30 € de perte max sur la venue
    euronext-paper:
      capital: 10000
      max_symbol_exposure_pct: 0.25   # 2 500 € par ticker
      symbol_risk_pct: 0.02           # 50 € par ticker
      venue_risk_pct: 0.05            # 500 € sur le livre actions
```

```yaml
# config/lifecycle.yaml — passe d'étude du backtest (§5.1)
backtest:
  reference_envelope: 1000        # enveloppe fixe, comportement intrinsèque
```

### Clés supprimées

`capital_allocator.*` disparaît **entièrement**, sauf `disabled_slots` qui migre
vers `risk.disabled_slots`.

Supprimées de `trading.*` : `max_positions`, `max_longs`, `max_shorts`,
`max_leverage` (porté par la venue), `risk_per_trade` (remplacé par
`risk.profile`).
Supprimée de `backtest.*` : `max_notional_pct`.

`trading.capital` disparaît : le capital est **par venue**. Toute lecture de
`cfg["trading"]["capital"]` doit être migrée vers l'enveloppe de venue.

### Validation au chargement (`_validate_risk_envelopes`, refus de démarrage)

1. Toute venue de `venues.defs` **atteignable** (default ou assign ou univers)
   doit avoir une enveloppe. Sinon → `ValueError`.
2. `0 < max_symbol_exposure_pct ≤ 1`, `0 < symbol_risk_pct ≤ 0.10`,
   `0 < venue_risk_pct ≤ 0.20`.
3. `venue_risk_pct ≥ symbol_risk_pct` — sinon le budget symbole est inatteignable.
4. WARNING si `venue_risk_pct ≥ n_symboles_attendus × symbol_risk_pct` :
   aucune décote de corrélation, le budget venue ne contraint rien.
5. WARNING si `capital × max_symbol_exposure_pct × min_slot_weight <
   venue.min_notional` : les plus petits slots ne pourront jamais trader.
6. ERREUR si `trade_risk_pct > symbol_risk_pct` — **garantie mono-slot**.

### La garantie mono-slot

`risk.profile` et `symbol_risk_pct` ne mesurent pas la même chose — l'un est le
risque d'**un** trade, l'autre plafonne la **somme** des risques ouverts sur le
symbole — mais ils se comparent, parce qu'ils s'appliquent à la même base dès
qu'un symbole n'a qu'un slot actif : ce slot porte un poids de 1,0, donc
`slot_envelope = symbol_envelope`.

`symbol_risk_pct < trade_risk_pct` rend alors ce slot incapable de passer le
moindre ordre — un bot vivant, promu, qui ne trade jamais, et rien dans les
logs pour le dire. C'est la panne silencieuse que cette refonte existe pour
supprimer : elle est donc **refusée au chargement**, pas diagnostiquée à chaud.

Corollaire de dimensionnement : avec N slots de poids `1/N`, la somme des
risques quand tous sont en position vaut exactement `trade_risk_pct`. Le budget
symbole *naturel* est donc `symbol_risk_pct = trade_risk_pct` ; toute marge
au-delà achète de la capacité de pyramidage. Réglage retenu au §7.1 :
`symbol_risk_pct = 2 × trade_risk_pct`, soit deux unités de risque plein.

---

## 4. Modules

### 4.1 `app/core/risk_envelope.py` — NOUVEAU

Modèle pur, sans état, testable sans I/O.

```python
@dataclass(frozen=True)
class Envelope:
    venue: str
    symbol: str
    slot_key: str
    currency: str
    venue_envelope: float       # capital de la venue
    venue_risk_budget: float
    symbol_envelope: float
    symbol_risk_budget: float
    slot_envelope: float        # base économique du bot — backtest ET live
    slot_risk_amount: float     # slot_envelope × trade_risk_pct
    max_leverage: float
    trade_risk_pct: float
    weight: float               # poids du slot dans son symbole

    @property
    def max_notional(self) -> float:   # slot_envelope × max(max_leverage, 1)

def slot_weights(edges: dict[str, float | None], min_weight: float) -> dict[str, float]
    """Poids par confiance d'edge, plancher appliqué puis renormalisation."""

def resolve_envelope(cfg, venue, symbol, slot_key, *, peers, edges) -> Envelope
    """`peers` = slots du même symbole ; `edges` = {slot_key: edge_ci_low|None}."""
```

### 4.2 `app/core/risk_ledger.py` — NOUVEAU

Comptabilité thread-safe du risque et du notionnel engagés. Remplace
`CapitalAllocator`.

```python
@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason_code: str = ""       # "" | budget_symbole | budget_venue | enveloppe_slot
                                #    | notionnel_min | levier
    detail: str = ""

class RiskLedger:
    def reserve(self, env: Envelope, *, risk: float, notional: float, pos_key: str) -> Decision
    def release(self, pos_key: str) -> None
    def update_risk(self, pos_key: str, risk: float) -> None   # trailing → libère
    def engaged(self) -> dict     # {venue: {...}, symbol: {...}, slot: {...}}
    def snapshot(self) -> dict    # pour /api + UI (§6)
```

Règles de `reserve`, dans l'ordre (le premier refus l'emporte, code de motif
renvoyé tel quel au compteur) :

1. `notional > env.max_notional` → `enveloppe_slot`
2. `symbol_notional + notional > env.symbol_max_notional` → `enveloppe_slot`
3. `symbol_risk + risk > env.symbol_risk_budget` → `budget_symbole`
4. `venue_risk + risk > env.venue_risk_budget` → `budget_venue`
5. `notional < venue.min_notional` → `notionnel_min`

La règle 2 porte sur `symbol_envelope × levier`, pas sur `symbol_envelope` :
les enveloppes sont libellées en **capital**, les plafonds de la règle 1 et de
la règle 2 en **notionnel**. Comme `Σ slot_envelope = symbol_envelope`, ce
plafond vaut exactement la somme des `max_notional` des slots du symbole.
Sans le levier, la règle 1 autoriserait à levier > 1 ce que la règle 2
refuserait — deux bases pour une même grandeur, le défaut même que cette
refonte supprime. Invariant vérifié par `tests/test_sizing_coherence.py`.

Aucune tolérance de dépassement (les `×1.05` actuels disparaissent) :
`reserve` est atomique sous verrou, la réservation précède l'ordre.

### 4.3 `app/core/rejections.py` — NOUVEAU

Compteurs partagés **live et backtest**, mêmes codes des deux côtés.

```python
REASONS = ("budget_symbole", "budget_venue", "enveloppe_slot", "notionnel_min",
           "levier", "venue", "risk", "slot_cb", "slot_disabled", "stop_invalide")

class RejectionCounter:
    def record(self, reason: str, *, venue: str, symbol: str, slot_key: str) -> None
    def as_dict(self) -> dict     # {total, par_motif, par_slot, par_symbole}
    def reset(self) -> None
```

Le `Backtester` en instancie un et le reporte dans `BacktestResult.to_dict()`
sous `rejections`. Le `LiveTrader` en tient un, exposé par `/api/status`.

### 4.4 `app/core/risk_sizer.py` — RÉÉCRIT

Signature unique, plus d'arguments optionnels :

```python
def compute_size(entry: float, stop_dist: float, env: Envelope,
                 size_factor: float = 1.0) -> tuple[float, float]:
    """(taille, notionnel). `stop_dist > 0` obligatoire — sinon ValueError."""
```

- Base = `env.slot_risk_amount`. **Une seule base.**
- `stop_dist ≤ 0` lève : le repli sur l'ATR brut est supprimé (c'était le chemin
  de sur-risque ×2,5). L'appelant compte `stop_invalide` et refuse le trade.
- `size_factor` (demi-Kelly stratégie) borné [0, 2], appliqué avant plafond.
- Le facteur de score interne (`0.5 + 0.5×…`) est **supprimé** : il modulait la
  taille sans être répliqué par le backtest, donc cassait la parité.

### 4.5 Intégration

| Fichier | Changement |
|---|---|
| `app/live/position_open_mixin.py` | résout l'`Envelope`, calcule `stop_dist`, `compute_size`, `ledger.reserve` ; un seul point de refus, un seul `record()` |
| `app/live/position_close_mixin.py` | `ledger.release(pos_key)` à la clôture |
| `app/live/trailing` (mise à jour du stop) | `ledger.update_risk(pos_key, nouveau_risque)` |
| `app/engine/backtest.py` | même `Envelope`, même `RiskLedger`, même `RejectionCounter` — c'est ce qui garantit la parité |
| `app/live/auto_opt_mixin.py` | `_lifecycle_thread` alimente `edges` pour les poids ; supprime `compute_shadow_allocation` / `apply_continuous_allocation` |
| `app/live/balance_sync.py` | `ledger.update_venue_capital(venue, equity)` au lieu de `allocator.update_equity` |
| `app/live/live_trader.py` | instancie `RiskLedger` + `RejectionCounter` ; supprime `allocator` |

---

## 5. Parité backtest ↔ live (le point critique)

**Le backtest d'un bot tourne sur l'enveloppe de ce bot**, pas sur un capital
global.

```python
bt = Backtester(engine, cfg, envelope=env)   # capital = env.slot_envelope
```

La passe `reference` réutilise la **même** `Envelope`, avec `slot_envelope`
remplacé par `backtest.reference_envelope` (`dataclasses.replace`) : mêmes
poids, mêmes plafonds relatifs, seule l'échelle change. C'est ce qui rend les
deux passes comparables.

Conséquences voulues :

- La quantification (`lot_size`, `fractional`), le `min_notional` et le plancher
  de courtage mordent **à la même échelle** des deux côtés. C'est la seule façon
  d'avoir une parité réelle sur actions.
- L'expectancy simulée et l'expectancy live sont exprimées en % de **la même
  base**, donc directement comparables.

### 5.1 Deux exécutions, deux questions différentes

Un backtest sur l'enveloppe réelle du slot (100 €) répond à « ce bot est-il
promouvable ? ». Il ne répond pas à « cette stratégie vaut-elle quelque chose ? ».
Les deux questions sont légitimes et **le backtest les traite en deux passes** :

| Passe | Enveloppe | Sert à |
|---|---|---|
| `reference` | `backtest.reference_envelope` (1 000 €, fixe) | étudier le comportement intrinsèque de la stratégie, indépendamment de l'allocation |
| `live` | `Envelope.slot_envelope` | parité live, promotion, rapport d'edge |

`Backtester.run()` renvoie les deux sous `runs: {reference, live}`. La passe
`live` seule fait foi pour la promotion (§5.2).

**Ce que l'écart mesure.** Le sizing est linéaire en enveloppe
(`taille = enveloppe × risk% / stop_dist`), donc **le PnL en % est invariant à
l'échelle** — tant qu'aucune contrainte absolue ne mord. Un écart entre les deux
passes ne peut donc venir que de :

- `min_notional` de la venue (200 € sur Euronext) → trades refusés à l'échelle réelle ;
- quantification (`fractional: false`, `lot_size`) → taille arrondie à 0 ;
- frais absolus (`fee_fixed`, `fee_min`) → non proportionnels ;
- saturation du budget symbole ou venue.

**L'écart nomme donc exactement le coût de la gestion du risque**, et les
`RejectionCounter` des deux passes disent lequel de ces quatre motifs l'explique.

Invariant testable qui en découle : sur une venue sans minimum, sans lot et sans
frais fixes (le cas crypto), les deux passes doivent produire **le même nombre de
trades et le même PnL %**, à l'arrondi près. Toute divergence est un bug de
proportionnalité.

**Coût d'exécution.** La double passe est réservée aux runs *rapportés* : UI
Laboratoire, forward-test, résultat final de l'optimiseur. **Pas par essai
d'optimisation** — l'optimiseur évalue ses trials sur la passe `live` seule
(c'est l'échelle qui tradera réellement), et ne produit la passe `reference` que
sur le meilleur jeu de paramètres retenu.

### 5.2 La base est enregistrée et vérifiée

`oos_tracker` enregistre, à côté de `edge` :

```json
"base": {"venue": "okx-margin", "slot_envelope": 100.0,
         "trade_risk_pct": 0.025, "currency": "EUR", "as_of": "2026-08-02"}
```

`SlotLifecycleManager._propose` refuse la promotion et journalise en WARNING si
la base enregistrée s'écarte de plus de **`base_drift_tolerance` (défaut 20 %)**
de l'enveloppe courante : l'edge a été mesurée sur une autre échelle
économique, elle doit être recalculée avant de servir à promouvoir.

`/api/bots` expose `base` pour chaque bot : le rapport d'edge dit désormais sur
quelle enveloppe il a été mesuré.

---

## 6. Observabilité

### 6.1 `/api/risk` — NOUVEAU

```json
{
  "venues": [{"venue": "okx-margin", "currency": "EUR",
              "envelope": 1000, "notional_engaged": 320,
              "risk_budget": 30, "risk_engaged": 12.4, "risk_pct_used": 0.41}],
  "symbols": [{"venue": "okx-margin", "symbol": "BTC/USDC",
               "envelope": 1000, "notional_engaged": 320,
               "risk_budget": 20, "risk_engaged": 12.4}],
  "slots":   [{"slot_key": "breakout::1h::BTC/USDC", "weight": 0.14,
               "envelope": 140, "risk_amount": 3.5, "risk_engaged": 3.5,
               "edge_ci_low": 0.42}],
  "total_risk_engaged": 12.4,
  "rejections": {"total": 37, "par_motif": {"budget_symbole": 21, "...": 0}}
}
```

- `BacktestResult.to_dict()` porte `envelope`, `rejections`, et les **deux
  passes** sous `runs: {reference: {...}, live: {...}}` (§5.1).
- La fiche de job d'optimisation porte `envelope` (comme `cost_model` aujourd'hui).
- UI : carte « Enveloppes & risque » sur `/portfolio` (jauges venue → symbole →
  slot) ; dans le Laboratoire, un bloc **« Étude vs Réel »** qui affiche côte à
  côte les deux passes, l'écart de PnL %, et les motifs de refus qui
  l'expliquent — c'est là que se lit le coût de la gestion du risque.
- Notification `on_trade_signal` : ajouter taille, notionnel, enveloppe et risque
  en devise (§7.2 — la venue actions notifie au lieu d'exécuter).

### 6.2 Diagnostics de faisabilité — `app/core/risk_diagnostics.py` NOUVEAU

Une config peut être syntaxiquement valide et **structurellement impossible** :
c'est exactement le défaut trouvé avant cette refonte (notionnel 200 € contre un
plafond de 95 €, refus systématique, jamais détecté). La faisabilité est
**analytique** — le sizing est en forme fermée, on n'a besoin d'aucune donnée de
marché pour savoir si un trade peut passer.

#### La fenêtre de stops viables

Pour un slot, en notant `stop_pct = stop_dist / prix` :

```
notional = slot.risk_amount / stop_pct        (plafonné à slot.max_notional)
```

Donc un trade n'est **exécutable** que si :

```
stop_pct ≤ slot.risk_amount / venue.min_notional        (sinon notionnel trop petit → refus)
stop_pct ≥ slot.risk_amount / slot.max_notional         (sinon le plafond mord → risque réel < risk%)
```

D'où la **fenêtre viable** `[risk_amount / max_notional , risk_amount / min_notional]`.
Fenêtre vide ⇒ **aucun trade ne passera jamais**, quelle que soit la volatilité.

Corollaire directement exploitable — le nombre maximum de slots par symbole :

```
n_slots_max = symbol_envelope × levier / venue.min_notional
```

*Exemple actions* : 2 500 € d'enveloppe ticker, levier 1, `min_notional` 200 €
→ **12 slots au plus**. À 15 slots, l'enveloppe par slot tombe à 166 € et plus
aucun trade n'est possible sur ce ticker.

#### Contrôles

```python
@dataclass(frozen=True)
class Diagnostic:
    severity: str      # "error" | "warning"
    code: str
    scope: str         # "venue:okx-margin" | "symbol:BTC/USDC" | "slot:breakout::1h::BTC/USDC"
    message: str       # constat + correctif chiffré
    values: dict       # les nombres qui l'ont déclenché

def diagnose(cfg, envelopes, *, prices=None) -> list[Diagnostic]
```

| Code | Sévérité | Condition | Correctif suggéré dans le message |
|---|---|---|---|
| `trade_impossible` | **error** | `slot.max_notional < venue.min_notional` | réduire le nombre de slots à `n_slots_max`, ou augmenter le capital de la venue |
| `lot_indivisible` | **error** | `slot.max_notional < prix × lot_size` (prix connu) | idem — l'enveloppe n'achète pas une unité |
| `trop_de_slots` | **error** | `n_slots > n_slots_max` sur un symbole | plafonner les slots du symbole |
| `enveloppe_venue_depassee` | **error** | `Σ max_symbol_exposure_pct > 1` sur les symboles attendus | réduire `max_symbol_exposure_pct` |
| `budget_venue_incoherent` | **error** | `venue_risk_pct < symbol_risk_pct` | budget symbole inatteignable |
| `fenetre_stops_etroite` | warning | fenêtre viable < 1,5× en ratio haut/bas | la plupart des stops seront refusés ou plafonnés |
| `plafond_notionnel_mordant` | warning | `risk_amount / max_notional > stop_pct_typique` | le risque réel sera **inférieur** au `risk_pct` configuré |
| `frais_fixes_dominants` | warning | `2 × fee_min / notional_typique > 0.01` | le plancher de courtage mange plus de 1 % du notionnel |
| `slot_sans_edge` | warning | poids nul (`edge_ci_low ≤ 0`) sur un slot déclaré actif | il n'échangera rien tant que l'edge n'est pas prouvée |
| `decote_correlation_absente` | warning | `venue_risk_pct ≥ Σ symbol_risk_pct` | le budget venue ne contraint rien |
| `min_slot_weight_insuffisant` | warning | `symbol_envelope × min_slot_weight < venue.min_notional` | les plus petits slots ne tradent jamais |

`stop_pct_typique` vient de `risk.diagnostics.stop_pct_reference` (défaut 0.025,
soit `trail_wide 2.5 × ATR 1 %`). Aucun accès marché : c'est un paramètre, pas
une mesure.

#### Vérifié sur les valeurs cibles du §7.1

| Configuration | Enveloppe slot | Fenêtre de stops | Verdict |
|---|---|---|---|
| BTC, 10 slots, levier 1 | 100 € | [2,50 % — ∞) | ⚠ `plafond_notionnel_mordant` |
| BTC, 10 slots, levier 3 | 100 € | [0,83 % — ∞) | ok |
| Ticker actions, 5 slots | 500 € | [2,50 % — 6,25 %] | ok |
| Ticker actions, 10 slots | 250 € | [2,50 % — 3,12 %] | ⚠ fenêtre étroite + frais fixes 1,6 % |
| Ticker actions, 15 slots | 167 € | vide | ❌ `trop_de_slots` + `trade_impossible` |

**Constat à assumer** : la cible crypto (10 slots, levier 1) déclenche
`plafond_notionnel_mordant` — au stop de référence 2,5 %, le notionnel calculé
(100 €) touche exactement le plafond, donc **tout stop plus serré verra son
risque réel tomber sous les 2,5 % configurés**. Deux correctifs possibles, à
trancher : passer `okx-margin` à `max_leverage: 3` (fenêtre [0,83 % — ∞), ce que
permet un compte margin), ou réduire le nombre de slots BTC. Le diagnostic est
conçu pour rendre ce genre d'arbitrage visible **avant** de trader, pas après.

#### Où ils se déclenchent

1. **Au chargement** (`load_config`) : les `error` **refusent le démarrage**, les
   `warning` sont journalisés. Même politique que `_validate_venues`.
2. **À chaque recalcul de poids** (lifecycle) : réévalués, journalisés en WARNING
   si nouveaux — un slot promu peut rendre les autres enveloppes infaisables.
3. **À la demande** : `GET /api/risk/diagnostics`.
4. **Avant écriture** : `POST /api/risk/envelopes` renvoie **400** si la nouvelle
   configuration produit une `error`, avec la liste — on ne sauvegarde jamais une
   config impossible.

### 6.3 Branchements UI

| Page / zone | Ce qui s'affiche | Source |
|---|---|---|
| Bandeau global (topbar) | pastille rouge + compte si `error`, orange si `warning` ; clic → `/settings?tab=risk` | `GET /api/risk/diagnostics` |
| `/portfolio` — carte **« Enveloppes & risque »** | jauges imbriquées venue → symbole → slot : enveloppe utilisée / budget de risque utilisé ; total du risque engagé en devise et en % | `GET /api/risk` |
| `/portfolio` — carte allocation | remplace `allocation-donut` / `allocations-grid` : part **par symbole** puis par slot, avec le poids d'edge | `GET /api/risk` |
| `/bots` — carte de bot | enveloppe du slot, poids, `edge_ci_low`, risque engagé / risque max | `GET /api/risk` + `/api/bots` |
| `/bots` — tiroir de bot | **base d'edge** (`slot_envelope`, `trade_risk_pct`, `as_of`) + badge « base dérivée » si > tolérance, avec l'écart chiffré | `/api/bots` (champ `base`) |
| `/lab` — onglet Backtest | bloc **« Étude vs Réel »** : les deux passes côte à côte, écart de PnL %, et le tableau des refus qui l'explique | `POST /api/backtest` (`runs`, `rejections`) |
| `/lab` — onglet Backtest | carte « Contexte facturé » (déjà livrée) + enveloppe utilisée | `cost_model`, `envelope` |
| `/lab` — onglet Optimizer | enveloppe du job + refus agrégés des essais | fiche de job |
| `/settings` — **nouvel onglet « Risque »** | édition des enveloppes par venue (capital, exposition symbole, risques), profil de risque, `min_slot_weight` ; **panneau de diagnostics** listant `error`/`warning` avec le correctif chiffré ; sauvegarde refusée si `error` | `GET /api/risk`, `POST /api/risk/envelopes`, `GET /api/risk/diagnostics` |
| `/trades` | colonne « risque engagé » par position (`|entrée − stop| × taille`) | position + `/api/risk` |

Règle d'affichage commune : **toujours montrer la base**. Un montant de risque
sans son enveloppe n'est pas interprétable — c'est précisément ce qui a laissé
passer la divergence backtest 1 000 € / live 90 €.

---

## 7. Réglages retenus (arbitrés)

### 7.1 Valeurs cibles

Profil de risque : `normal` ⇒ `trade_risk_pct = 0.025`.

| Venue | capital | `max_symbol_exposure_pct` | `symbol_risk_pct` | `venue_risk_pct` |
|---|---|---|---|---|
| `margin-isolated` | 1 000 € | **1.00** (BTC seul) | 0.05 → **50 €** | 0.05 → 50 € |
| `euronext-paper` | 10 000 € | 0.25 (2 500 €/ticker) | 0.05 → **125 €** | 0.05 → 500 € |

**BTC à 100 % ⇒ enveloppe symbole = capital entier de la venue.** Le risque d'un
trade y vaut donc 25 € (2,5 % de 1 000), et le budget symbole 50 € — soit
`2 × trade_risk_pct`, conformément à la garantie mono-slot du §3 : un slot seul
doit pouvoir entrer *et* pyramider une fois. Un budget à 0.02 (20 €) rendrait ce
slot incapable de passer le moindre ordre et fait désormais échouer le
chargement.

Le budget de risque **venue** est la décote de corrélation : volontairement
**sous** la somme des budgets symbole dès qu'il y a plusieurs symboles. Avec un
seul symbole il ne peut pas mordre (d'où le WARNING `decote_correlation_absente`,
attendu ici) ; il devient le garde-fou dès l'ajout d'ETH — il faudra alors le
tenir sous `2 × symbol_risk_pct`. C'est lui qui remplace la règle de corrélation
directionnelle supprimée (§2.2).

### 7.2 Euronext reste `can_execute: false` — ce que ça change

L'exécution actions n'est pas implémentée : le bot **notifie** le trade et
l'humain le passe à la main. Conséquence à ne pas manquer :

> l'enveloppe actions ne dimensionne pas un ordre, elle dimensionne **une
> recommandation**. La taille notifiée est celle qu'un humain va exécuter.

Le modèle de risque doit donc être exact **avant** toute exécution automatique —
c'est même le cas où une erreur de sizing coûte le plus cher, puisqu'aucun
garde-fou d'exchange ne viendra la rattraper. La notification `on_trade_signal`
doit donc porter, en plus du symbole / sens / prix / stop : **la taille, le
notionnel, l'enveloppe et le risque en devise** qui l'ont produite.

À signaler sans le remettre en cause : à 1 000 € / 10 000 €, l'essentiel du
risque du système est côté actions, donc côté exécution manuelle.

### 7.3 Ce qu'il reste à accepter — la dérive de base

L'enveloppe d'un slot **bouge** quand les poids d'edge changent. Une edge mesurée
sur 100 € puis appliquée à 140 € n'a pas été validée à cette échelle. D'où
`base_drift_tolerance: 0.20` (§5.2) plutôt qu'un recalcul permanent : au-delà de
20 % d'écart, la promotion est refusée et l'edge doit être rejouée.

---

## 8. Suppressions (même passe, pas de dépréciation)

**Fichier supprimé** : `app/live/capital_allocator.py` (879 l.) et
`tests/test_capital_allocator.py`, `tests/test_allocator_persistence.py`,
`tests/test_allocator_thread_safety.py` (réécrits sur `RiskLedger`).

**Membres supprimés** : `CapitalAllocator` en entier, `RiskGate.can_trade`
(partie `max_positions`/`max_longs`/`max_shorts`), `check_correlation`,
`can_allocate`, `slot_budget_usdc`, `budget_pct`, `rebuild_slots`,
`_equalize_budgets`, `_apply_mode`, `_apply_manual_budgets`,
`compute_shadow_allocation`, `apply_continuous_allocation`, `rebalance_if_due`,
`force_rebalance`, `set_slot_budget`, `set_max_slot_pct`, `set_mode`,
`set_rebalance_interval`, `enabled_budgets`, `per_bot_sizing`.

**Config supprimée** : tout `capital_allocator.*` (sauf `disabled_slots` migré),
`trading.{capital,risk_per_trade,max_positions,max_longs,max_shorts,max_leverage}`,
`backtest.max_notional_pct`.

**API supprimée** : `POST /api/config/allocator*`, `POST /api/slots/*/budget`,
`POST /api/allocator/rebalance`. Remplacées par `/api/risk` (lecture) et
`POST /api/risk/envelopes` (écriture des enveloppes).

**Frontend** : composants d'allocation par slot (`allocation-donut`,
`allocations-grid`) réécrits sur `/api/risk`.

---

## 9. Tests exigés

| Fichier | Ce qui est verrouillé |
|---|---|
| `tests/test_risk_envelope.py` | poids par confiance d'edge (nul si `ci_low ≤ 0`, égal si aucune edge, plancher + renormalisation) ; enveloppes emboîtées ; `max_notional` = enveloppe × levier (spot → levier 1) |
| `tests/test_risk_ledger.py` | ordre des refus et code de motif ; `reserve`/`release` symétriques ; `update_risk` libère du budget au trailing ; atomicité sous 50 threads concurrents ; **aucun dépassement du budget symbole ou venue, jamais** |
| `tests/test_sizing_coherence.py` | **le notionnel produit par `compute_size` est toujours acceptable par `reserve`** — c'est le test qui aurait attrapé le bug des deux bases ; sur 500 cas générés (ATR, prix, poids, levier variés) |
| `tests/test_backtest_live_parity.py` | même `Envelope` + même signal ⇒ même taille, même notionnel, même motif de refus des deux côtés |
| `tests/test_backtest_dual_run.py` | **invariance d'échelle** : sur venue crypto (fractional, sans `min_notional` ni frais fixes), `reference` et `live` donnent le même nombre de trades et le même PnL % ; sur venue actions (`min_notional: 200`, titres entiers), l'écart existe **et** est expliqué par les compteurs (`notionnel_min` / `enveloppe_slot`) ; l'optimiseur ne produit `reference` que sur le meilleur jeu de params, jamais par essai |
| `tests/test_edge_base_guard.py` | promotion refusée si la base enregistrée dérive > 20 % ; `base` présente dans `/api/bots` |
| `tests/test_rejections.py` | mêmes codes live et backtest ; compteurs exposés dans les deux payloads |
| `tests/test_config_risk_envelopes.py` | les 5 règles de validation du §3 |
| `tests/test_risk_diagnostics.py` | chaque code du §6.2 se déclenche sur un cas construit **et ne se déclenche pas** sur un cas sain ; la fenêtre de stops est calculée juste (valeurs du tableau « vérifié sur les valeurs cibles ») ; un `error` refuse le démarrage et fait échouer `POST /api/risk/envelopes` en 400 ; `n_slots_max` est exact |
| `tests/test_ui_contracts.py` (vitest) | schémas zod de `/api/risk` et `/api/risk/diagnostics` ; la carte Enveloppes affiche toujours la base à côté du montant ; le badge de dérive apparaît au-delà de la tolérance |

---

## 10. Ordre d'implémentation

1. `risk_envelope.py` + `risk_ledger.py` + `rejections.py` + leurs tests.
2. `_validate_risk_envelopes` dans `config.py` + `config/risk.yaml` cible,
   puis `risk_diagnostics.py` branché au chargement (§6.2).
3. `risk_sizer.compute_size` réécrit + `test_sizing_coherence`.
4. Branchement live (`position_open_mixin`, `position_close_mixin`, trailing,
   `live_trader`, `balance_sync`, `auto_opt_mixin`).
5. Branchement backtest (`backtest.py`) + double passe `reference`/`live`
   + `test_backtest_live_parity` + `test_backtest_dual_run`.
6. `oos_tracker.base` + garde de dérive + lifecycle.
7. `/api/risk`, `/api/risk/diagnostics`, `POST /api/risk/envelopes`, payloads
   backtest/optimiseur, notification `on_trade_signal` enrichie, puis **tous** les
   branchements UI du tableau §6.3 (bandeau, `/portfolio`, `/bots`, `/lab`,
   `/settings` onglet Risque, `/trades`).
8. Suppressions du §8 — **en dernier**, quand plus rien n'y renvoie.

Convention de commentaires : une ligne pour le « pourquoi » quand il n'est pas
évident, rien pour le « quoi ». Pas de blocs explicatifs.

**Critère d'acceptation global** : la suite complète passe, et sur la config
livrée un backtest `breakout::1h::BTC/USDC` produit une taille strictement
acceptée par `RiskLedger.reserve` — ce qui était impossible avant cette refonte.
