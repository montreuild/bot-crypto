# Choix d'exchange & modèle de marché pour le netting

> Réponse à : « j'aime le netting (réduction des frais entre bots), mais l'Option A est limitée par
> Binance — un autre exchange type OKX est-il mieux ? »
>
> Complète `REVUE_CRITIQUE_VISION_CIBLE.md` (qui pose le choix Option A vs Option B / netting).

---

## 1. Le netting est un artefact du couple spot + margin isolé, pas une fatalité

Le moteur de netting (Option B) existe pour faire coexister un bot A (short) et un bot B (long) sur
le même symbole en ne payant le coût que sur l'**exposition nette**. Sur Binance spot/margin isolé,
c'est impossible proprement (un wallet isolé par paire, pas de long+short distincts dans le même
wallet) → d'où la machinerie : registre virtuel, ordres delta, stops logiciels, réconciliation.

**Ce besoin disparaît si l'on change de modèle de marché**, pas forcément d'exchange.

## 2. Les perpétuels en hedge mode = le netting natif

Les **futures perpétuels en mode hedge** (hedge mode / position mode) permettent de tenir un **long
ET un short sur le même contrat, dans un seul compte, sous une marge mutualisée**. L'exchange
maintient lui-même deux positions distinctes : prix d'entrée séparés, PnL séparé, liquidation
séparée. **C'est exactement la « position virtuelle par bot » de la vision — mais tenue par
l'exchange, pas par du code.**

| Aspect | Option B (netting logiciel, Binance spot/margin) | Perps hedge mode (OKX/Binance/Bybit) |
|---|---|---|
| Positions par bot | Virtuelles, dans un registre maison | **Natives**, tenues par l'exchange |
| Coût sur exposition nette | Oui (un seul ordre delta) | Oui (funding des deux jambes se compense ≈ funding sur le net) |
| Stops par bot | Logiciels + filet exchange sur le net | Stops exchange par position (natifs) |
| Réconciliation | Industrielle, fragile (cf. revue §2) | Inutile — l'exchange est la source de vérité |
| Liquidation partagée | Oui (wallet isolé partagé) — risque | Par position, gérée par le portfolio margin |

**Conclusion : hedge mode domine le netting logiciel.** On obtient le bénéfice économique du netting
(coût sur le net) sans construire le composant le plus risqué de la vision.

## 3. OKX vs Binance dans ce cadre

Une fois sur les perps, Binance, OKX et Bybit supportent tous hedge mode + portfolio/unified margin.
Nuances :

- **OKX** — *unified account* (spot + margin + perps + options sous marge de portefeuille
  mutualisée) considéré comme le plus mature/élégant pour piloter plusieurs positions sous une marge
  nette. Vrai point fort, historiquement pionnier.
- **Binance** — possède aussi *Portfolio Margin* + hedge mode sur les futures, donc capable de la
  même chose, mais structure de wallets plus fragmentée et PM historiquement réservé aux paliers
  élevés. Liquidité généralement la plus profonde.
- **Bybit** — *Unified Trading Account* comparable à OKX, réputé propre également.

**Formulation juste : ce n'est pas « OKX > Binance », c'est « n'importe lequel des trois en perps +
hedge mode + portfolio margin > Binance-spot-avec-netting-logiciel ».** Sur l'élégance du compte
unifié multi-positions, OKX/Bybit ont une légère avance ; pas un écart décisif.

> ⚠ Détails volatils à vérifier au moment du choix (non figés ici) : grilles de frais maker/taker,
> seuils d'éligibilité au portfolio margin, et surtout **l'accès retail aux dérivés en UE/France**
> (MiCA, restrictions selon statut). À confirmer avant tout engagement.

## 4. Le vrai coût caché : passer aux perps change l'économie du backtest

Point critique pour la fidélité :

- Les stratégies sont backtestées sur **OHLCV spot**. Les perps suivent le spot mais ajoutent le
  **funding** (≈ toutes les 8 h) et un léger basis. Tant que le moteur de backtest ne modélise pas
  le funding, le forward-test glissant et le contrat Monte-Carlo sont **biaisés** → on récrée un
  écart live/backtest là où on cherchait à le fermer.
- Le funding est un **nouveau flux de coût/revenu** à intégrer au sizing, au score et à la compta
  par bot.
- La **liquidation** est plus tranchante avec levier.
- **CCXT** abstrait Binance/OKX/Bybit : switch faisable mais quirks par exchange (position mode,
  paramètres d'ordre, rate limits). Coût borné, non nul.

**Condition non négociable avant de basculer sur les perps : ajouter le funding au moteur de
backtest.**

## 5. Recommandation

La décision n'est pas « Binance ou OKX » d'abord, mais **« spot/margin ou perpétuels »** :

1. **Rester en spot/margin** → **Option A** (bots indépendants, quotes séparées pour les paires
   opposées) reste le choix sain ; l'exchange importe peu.
2. **Vouloir le netting** → **passer aux perps en hedge mode**, où OKX (ou Bybit) offre le compte
   unifié le plus propre. On obtient le netting **nativement**, sans coder la réconciliation.

**Chemin conseillé :**

- Garder l'abstraction CCXT et faire du **modèle de marché (spot/perp) et de l'exchange un attribut
  du `venue` par bot** (déjà prévu dans la vision, §6.4).
- Démarrer **spot / Option A** pour prouver la boucle forward-test → budget → cycle de vie (peu de
  risque, compta triviale).
- Introduire ensuite un venue **« perp hedge » (OKX)** pour les bots qui ont besoin de short à
  levier — netting natif, zéro moteur de réconciliation maison.
- Préalable bloquant à cette 2e étape : **funding modélisé dans le backtest**.

Ainsi on récupère le bénéfice du netting que vous aimez, sans payer la dette technique de l'Option B,
et sans pari « big bang » sur un changement d'exchange.
