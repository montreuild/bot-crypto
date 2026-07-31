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
  // ⚠ Pas de `rewrites()` vers /api : c'est le route handler
  // `src/app/api/[...path]/route.ts` qui proxifie, parce qu'un rewrite ne peut
  // pas injecter l'en-tête `X-API-Key` (cf. le commentaire du handler).
  async rewrites() {
    return [
      { source: '/health', destination: `${process.env.BOT_API_URL || 'http://localhost:8000'}/health` },
    ];
  },
  // S0-F1-US6 — WebSocket : Next.js ne proxy pas les WS nativement, on laisse
  // le client se connecter directement. En dev, défaut `ws://localhost:8000/ws`.
  // En prod, NEXT_PUBLIC_WS_URL DOIT être définie (sinon build échoue en
  // production pour éviter un WS silencieusement cassé).
  // On utilise une résolution relative côté client : si le navigateur est sur
  // https://example.com, le WS sera wss://example.com/ws — pattern same-origin.
  env: {
    NEXT_PUBLIC_WS_URL:
      process.env.NEXT_PUBLIC_WS_URL ||
      // En dev (NODE_ENV !== production), localhost est OK
      (process.env.NODE_ENV === 'production'
        ? ''  // Vide en prod sans config — fallback runtime côté client
        : 'ws://localhost:8000/ws'),
  },
};

export default nextConfig;
