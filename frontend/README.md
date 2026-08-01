# Crypto Bot — Frontend Next.js

Frontend moderne pour le bot de trading crypto, avec streaming temps réel via WebSocket.

## Stack

- **Next.js 15** (App Router, React Server Components)
- **React 19**
- **TypeScript 5.7**
- **Tailwind CSS 3.4** (dark mode natif)
- **TanStack Query 5** (data fetching + cache)
- **Recharts** (charts analytics)
- **Radix UI** (composants accessibles)
- **Lucide Icons**
- **Sonner** (notifications toast)
- **WebSocket natif** (streaming temps réel)

## Pages

L'UI tient en **5 pages méta** à onglets, plus quelques pages dédiées.

| Route | Description |
|---|---|
| `/portfolio-v2` | Vue temps réel : KPIs, equity curve, positions, trades live, signals feed, allocations, risk panel, journal de notifications, bots à edge significatif |
| `/bots-v2` | Portefeuille de stratégies avec cycle de vie (candidat → essai → actif → retiré) |
| `/lab` | Laboratoire — onglets `backtest`, `optimizer`, `ml`, `replay`, `compare` |
| `/market` | Marché — onglets `scanner`, `smartgraph`, `smartreplay`, `derivatives` |
| `/settings-v2` | Réglages — onglets `capital`, `notifications`, `data`, `audit`, `ui` |
| `/trades` | Historique des trades avec filtres + export CSV |
| `/models` | Registre ML versionné (gate de promotion, pin, sweep) |
| `/data` | Cache OHLCV, refetch, backfill |
| `/audit`, `/audit-log` | Audit OOS et journal d'audit |

L'onglet actif se pilote par `?tab=` : `/market?tab=smartgraph` est un lien
profond partageable, et c'est la cible des redirections ci-dessous.

### Routes héritées

14 anciennes routes sont des **redirections 308** de compatibilité, déclarées
dans `redirects()` de `next.config.mjs` — leurs pages ont été supprimées :

| Ancienne route | Cible |
|---|---|
| `/dashboard`, `/portfolio` | `/portfolio-v2` |
| `/bots` | `/bots-v2` |
| `/backtest`, `/optimizer`, `/ml`, `/replay`, `/compare` | `/lab?tab=…` |
| `/scanner`, `/smartgraph`, `/smartreplay`, `/derivatives` | `/market?tab=…` |
| `/config`, `/settings` | `/settings-v2?tab=capital` |

⚠ Un 308 est mis en cache durablement par les navigateurs : revenir en arrière
exige de vider le cache côté client. Passer `permanent: false` (307) tant qu'une
cible n'est pas validée.

## Démarrage rapide

### Prérequis

- Node.js 20+ (recommandé : via [nvm](https://github.com/nvm-sh/nvm))
- Backend FastAPI qui tourne sur `http://localhost:8000` (cf. `scripts/setup.sh` à la racine du repo)

### Installation

```bash
cd frontend
npm install        # ou pnpm install / bun install
```

### Configuration

Copiez `.env.example` en `.env.local` et ajustez si besoin :

```bash
cp .env.example .env.local
```

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
```

### Lancement

```bash
npm run dev
```

→ Ouvrez **http://localhost:3000**

### Build production

```bash
npm run build
npm start
```

## Architecture

```
src/
├── app/                          # Next.js App Router
│   ├── layout.tsx                # Layout racine (sidebar + topbar)
│   ├── globals.css               # Styles globaux + Tailwind
│   ├── dashboard/page.tsx        # Dashboard principal
│   ├── bots/page.tsx             # Portefeuille de bots
│   ├── trades/page.tsx           # Historique trades
│   ├── backtest/page.tsx         # Backtest interactif
│   ├── config/page.tsx           # Configuration
│   └── scanner/page.tsx          # Scanner SMC
├── components/
│   ├── providers.tsx             # QueryClient + WebSocket + Tooltip providers
│   ├── layout/
│   │   ├── sidebar.tsx           # Navigation latérale
│   │   └── topbar.tsx            # Topbar avec boutons Start/Stop, PnL, WS status
│   ├── ui/
│   │   ├── card.tsx              # Card, CardHeader, CardTitle, CardContent
│   │   ├── button.tsx            # Button (variants: primary, success, danger, ghost, outline)
│   │   ├── badge.tsx             # Badge (variants: success, danger, warning, info, purple)
│   │   └── toaster.tsx           # Toaster (sonner)
│   ├── cards/
│   │   ├── kpi-cards.tsx         # CapitalCard, PnLCard, WinRateCard, etc.
│   │   ├── positions-table.tsx   # Positions ouvertes avec PnL unrealized
│   │   ├── live-trades-feed.tsx  # Feed temps réel des ouvertures/fermetures (WS)
│   │   ├── signals-feed.tsx      # Feed temps réel des signaux (WS + historique)
│   │   ├── allocations-grid.tsx  # Grille d'allocation par slot
│   │   └── risk-panel.tsx        # Drawdown gauges + circuit breakers
│   └── charts/
│       └── equity-curve.tsx      # Equity curve Recharts
├── hooks/
│   └── use-api.ts                # Hooks TanStack Query (useBotStatus, useBots, useTrades, etc.)
├── lib/
│   ├── api.ts                    # Client API fetch (api.getStatus(), api.startBot(), etc.)
│   ├── ws-provider.tsx           # WebSocketProvider + hooks (useTradeEvents, useSignalEvents, etc.)
│   └── utils.ts                  # cn(), formatUSD, formatPct, lifecycleStyle, etc.
└── types/
    └── index.ts                  # Types TypeScript pour toutes les réponses API
```

## WebSocket temps réel

Le frontend se connecte automatiquement à `ws://localhost:8000/ws` au mount. Reconnexion automatique avec backoff exponentiel (max 30s).

### Events reçus

| Type | Description | Hook |
|---|---|---|
| `trade.opened` | Position ouverte | `useTradeEvents()` |
| `trade.closed` | Position fermée | `useTradeEvents()` |
| `signal.generated` | Signal généré (accepté ou rejeté) | `useSignalEvents()` |
| `risk.circuit_breaker` | Circuit breaker déclenché | `useRiskEvents()` |
| `risk.drawdown_warning` | Alerte drawdown | `useRiskEvents()` |
| `cycle.update` | Mise à jour du cycle | `useCycleUpdates()` |
| `ticker.update` | Mise à jour prix | `useLiveTickers()` |

### Exemple d'usage

```tsx
'use client';
import { useTradeEvents } from '@/lib/ws-provider';

function MyComponent() {
  const { openedTrades, closedTrades } = useTradeEvents();
  return (
    <div>
      <h3>{openedTrades.length} trades ouverts en direct</h3>
      {openedTrades.map(evt => (
        <div key={evt.ts}>
          {evt.data.symbol} @ ${evt.data.entry}
        </div>
      ))}
    </div>
  );
}
```

## Développement

### Ajouter une page

1. Créer `src/app/<route>/page.tsx`
2. Ajouter l'entrée dans `src/components/layout/sidebar.tsx`
3. Utiliser les hooks de `src/hooks/use-api.ts` pour fetch les données

### Ajouter un hook API

```typescript
// src/hooks/use-api.ts
export function useMyData() {
  return useQuery({
    queryKey: ['myData'],
    queryFn: () => api.getMyData(),
    refetchInterval: 5000,
  });
}
```

### Ajouter un event WebSocket

Côté backend (`app/core/events.py`) :
```python
def publish_my_event(**kwargs):
    event_hub.publish({"type": "my.event", "data": kwargs})
```

Côté frontend (`src/lib/ws-provider.tsx`) :
```typescript
export function useMyEvents() {
  const { subscribe } = useWebSocket();
  const [events, setEvents] = useState<WSEvent[]>([]);
  useEffect(() => {
    const unsub = subscribe((event) => {
      if (event.type === 'my.event') {
        setEvents(prev => [event, ...prev].slice(0, 50));
      }
    });
    return unsub;
  }, [subscribe]);
  return events;
}
```

## Sécurité

- En local sans `web.api_key` : le backend accepte les requêtes depuis `127.0.0.1` uniquement
- En production : définir `web.api_key` côté backend. Les pages web posent alors
  un cookie HttpOnly `api_key`, envoyé automatiquement par le navigateur
  (`fetch` avec `credentials: 'include'`, WebSocket, `EventSource` avec
  `withCredentials: true`) — aucune clé à configurer côté frontend (S1-05 :
  une variable `NEXT_PUBLIC_*` serait visible dans le bundle JS client)
- WebSocket : même règle ; `?api_key=xxx` dans l'URL reste un fallback pour
  les clients non-navigateur (visible dans les logs d'accès, à éviter sinon)

## Dépannage

### Le dashboard reste vide

1. Vérifier que le backend tourne : `curl http://localhost:8000/health`
2. Vérifier `.env.local` : `NEXT_PUBLIC_API_URL=http://localhost:8000`
3. Ouvrir la console navigateur (F12) → onglet Network → regarder les requêtes `/api/*`

### WebSocket ne se connecte pas

1. Vérifier l'URL dans `.env.local` : `NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws`
2. Si le backend a `web.api_key` défini, ajouter `?api_key=xxx` à `NEXT_PUBLIC_WS_URL`
3. Console navigateur → onglet Console → messages `[WS]`

### Erreurs CORS

Le frontend utilise directement `NEXT_PUBLIC_API_URL` (pas de proxy). Si vous deployez le frontend sur un domaine différent du backend, configurez `ALLOWED_ORIGINS` côté backend.

## License

MIT (identique au backend)
