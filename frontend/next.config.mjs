import fs from 'node:fs';
import path from 'node:path';

/**
 * Lit la `WEB_API_KEY` du `.env` à la racine du dépôt (celui que génère
 * setup.sh et que lit le backend) et la pose dans `process.env` **du process
 * serveur Next**. Elle est consommée par le proxy `src/app/api/[...path]`,
 * jamais exposée au bundle client — d'où l'absence de préfixe NEXT_PUBLIC_.
 *
 * Une variable déjà présente dans l'environnement reste prioritaire.
 */
function loadRootEnv() {
  if (process.env.WEB_API_KEY) return;
  try {
    const raw = fs.readFileSync(path.join(process.cwd(), '..', '.env'), 'utf8');
    for (const line of raw.split(/\r?\n/)) {
      const m = line.match(/^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/i);
      if (!m) continue;
      const [, key, rawVal] = m;
      if (key !== 'WEB_API_KEY') continue;
      process.env[key] = rawVal.trim().replace(/^(['"])(.*)\1$/, '$2');
      break;
    }
  } catch {
    // Pas de .env à la racine : le backend tournera sans clé (accès localhost
    // uniquement), le proxy fonctionne alors sans en-tête d'auth.
  }
}

loadRootEnv();

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Image Docker (Dockerfile.frontend) : build standalone minimal.
  output: 'standalone',

  // ⚠ Pas de `rewrites()` vers /api : c'est le route handler
  // `src/app/api/[...path]/route.ts` qui proxifie, parce qu'un rewrite ne peut
  // pas injecter l'en-tête `X-API-Key` (cf. le commentaire du handler).
  async rewrites() {
    const backend = process.env.BOT_API_URL || 'http://localhost:8000';
    return [
      { source: '/health', destination: `${backend}/health` },
      // WebSocket temps réel — MÊME ORIGINE que les pages, comme le reste.
      //
      // Le client se connectait auparavant en direct sur `ws://localhost:8000`,
      // donc sur une AUTRE origine que la page (`:3000`). Deux conséquences :
      // le cookie d'auth `api_key`, posé sur l'origine Next, n'était jamais
      // envoyé, et le backend refusait la poignée de main par un 403 muet.
      //
      // En production, `deploy/nginx.conf` fait déjà exactement ça (`location
      // /ws` → :8000) : ce rewrite aligne le dev sur la prod au lieu de laisser
      // les deux diverger.
      { source: '/ws', destination: `${backend}/ws` },
    ];
  },

  /**
   * S10 — Bascule strangler fig : les anciennes routes cèdent la place aux pages
   * méta. `permanent: true` émet un **308**.
   *
   * ⚠ Une redirection n'est posée QUE lorsque la cible porte réellement la
   * fonctionnalité. Tant que l'onglet visé était une carte de renvoi vers
   * l'ancienne page, la 308 aurait bouclé — c'est pourquoi 11 des 14
   * redirections du plan sont restées en attente jusqu'aux lots de fusion.
   *
   * Lot Marché : les quatre onglets de `/market` montent le contenu des
   * anciennes pages (`src/components/views/`), les 308 sont donc posées.
   * La query est conservée par Next : `/scanner?symbol=X&tf=Y` arrive sur
   * `/market?tab=scanner&symbol=X&tf=Y`, que la page relit.
   *
   * ⚠ Un 308 est mis en cache durablement par les navigateurs. Repasser en
   * arrière exige de vider le cache côté client : basculer `permanent` à
   * `false` (307) tant que la validation utilisateur n'est pas faite.
   */
  async redirects() {
    return [
      // S11 — fin de la migration : les pages canoniques perdent le suffixe
      // `-v2`, qui datait de la coexistence avec les pages Jinja2. Les URLs
      // `-v2` restent redirigées : elles ont vécu en prod, sont dans les
      // favoris et dans des 308 déjà mises en cache par les navigateurs.
      { source: '/portfolio-v2', destination: '/portfolio', permanent: true },
      { source: '/bots-v2', destination: '/bots', permanent: true },
      { source: '/settings-v2', destination: '/settings', permanent: true },

      { source: '/dashboard', destination: '/portfolio', permanent: true },
      { source: '/backtest', destination: '/lab?tab=backtest', permanent: true },

      // Lot Marché
      { source: '/scanner', destination: '/market?tab=scanner', permanent: true },
      { source: '/smartgraph', destination: '/market?tab=smartgraph', permanent: true },
      { source: '/smartreplay', destination: '/market?tab=smartreplay', permanent: true },
      { source: '/derivatives', destination: '/market?tab=derivatives', permanent: true },

      // Lot Laboratoire. ⚠ `/models` n'y figure PAS : le registre versionné
      // n'est pas dans le plan de fusion et reste une page à part entière.
      { source: '/optimizer', destination: '/lab?tab=optimizer', permanent: true },
      { source: '/ml', destination: '/lab?tab=ml', permanent: true },
      { source: '/replay', destination: '/lab?tab=replay', permanent: true },
      { source: '/compare', destination: '/lab?tab=compare', permanent: true },

      // Lot Réglages. `/settings` comme `/config` atterrissent sur Capital :
      // c'était le premier bloc des deux pages (presets de risque d'un côté,
      // stratégies de l'autre). Thème et notifications sont dans l'onglet UI.
      // `/config` atterrit sur Capital : c'était le premier bloc des deux
      // pages. `/settings` n'est plus une redirection — c'est la page.
      { source: '/config', destination: '/settings?tab=capital', permanent: true },
    ];
  },
  // S0-F1-US6 — WebSocket : plus de valeur par défaut cross-origin.
  //
  // Le défaut `ws://localhost:8000/ws` faisait sortir le WS de l'origine de la
  // page (`:3000`), alors que tout le reste y transite : le REST par le route
  // handler `/api/[...path]`, `/health` et `/ws` par rewrite en dev, `/ws` par
  // nginx en prod. Conséquence directe : le cookie d'authentification, posé sur
  // l'origine Next, n'accompagnait jamais la poignée de main.
  //
  // Le client retombe désormais sur le same-origin (`ws(s)://<page>/ws`) —
  // une seule règle, un seul chemin d'authentification, en dev comme en prod.
  // `NEXT_PUBLIC_WS_URL` reste honorée si un déploiement expose réellement le
  // WS sur un autre hôte ; ce n'est plus le comportement par défaut.
  env: {
    NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL || '',
  },
};

export default nextConfig;
