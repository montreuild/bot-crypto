/**
 * Proxy same-origin vers le backend FastAPI.
 *
 * Pourquoi un route handler plutôt que le `rewrites()` de next.config :
 *
 * 1. **Auth** — depuis la fin de Jinja2 (S6-09), plus rien ne pose le cookie
 *    HttpOnly `api_key` : c'était le rôle de `_tpl()` dans `app/api/main.py`,
 *    supprimé avec les templates. Dès que `web.api_key` est renseigné (ce qui
 *    est le cas dès que `.env` est chargé), toutes les routes protégées
 *    répondaient 403 au frontend, qui n'avait aucun moyen d'envoyer la clé.
 *    Le proxy l'injecte ici, **côté serveur** : elle ne transite jamais par le
 *    bundle client, contrairement à un `NEXT_PUBLIC_API_KEY`.
 *
 * 2. **CORS** — le navigateur ne parle plus qu'à son propre origine. Plus de
 *    préflight `OPTIONS`, plus de whitelist d'origines à tenir à jour.
 *
 * Un `rewrites()` ne permet pas d'ajouter un en-tête, d'où le handler.
 *
 * 3. **Cookie du WebSocket** — le navigateur ouvre le WS lui-même et ne peut
 *    pas poser d'en-tête : son seul credential est le cookie de l'origine.
 *    Ce proxy repose donc l'`api_key` HttpOnly que `_tpl()` posait avant la
 *    fin de Jinja2 (cf. le bloc dédié plus bas). Le WS passe par la même
 *    origine que les pages — rewrite `/ws` en dev, `location /ws` de nginx en
 *    prod — donc le cookie l'accompagne.
 */

import { NextRequest } from 'next/server';

const BACKEND = (process.env.BOT_API_URL || 'http://localhost:8000').replace(/\/$/, '');
const API_KEY = process.env.WEB_API_KEY || '';

// Le proxy doit toujours toucher le backend : aucune mise en cache.
export const dynamic = 'force-dynamic';

// En-têtes propres au hop client→proxy, à ne pas réémettre vers le backend.
const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade', 'host', 'content-length',
  'accept-encoding',
]);

async function proxy(req: NextRequest, path: string[]): Promise<Response> {
  const search = req.nextUrl.search;
  const target = `${BACKEND}/api/${path.join('/')}${search}`;

  const headers = new Headers();
  req.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) headers.set(key, value);
  });
  if (API_KEY) headers.set('X-API-Key', API_KEY);

  const method = req.method;
  const hasBody = method !== 'GET' && method !== 'HEAD';

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
      redirect: 'manual',
      cache: 'no-store',
    });
  } catch (cause) {
    // Backend éteint : on renvoie un 503 que le client sait interpréter
    // (`ApiError(status: 0)` côté api.ts affiche « Backend injoignable »).
    return Response.json(
      {
        detail:
          `Backend injoignable sur ${BACKEND} — le serveur FastAPI est-il démarré ?`,
        cause: String((cause as Error)?.message ?? cause),
      },
      { status: 503 },
    );
  }

  const outHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    // `content-encoding`/`content-length` décrivent le corps déjà décodé par
    // fetch : les réémettre produirait une réponse illisible.
    if (!['content-encoding', 'content-length', 'transfer-encoding'].includes(key.toLowerCase())) {
      outHeaders.set(key, value);
    }
  });

  // ── Cookie d'auth pour le WebSocket ──────────────────────────────────────
  //
  // Le REST passe par ce proxy, qui injecte `X-API-Key` côté serveur. Le
  // WebSocket, lui, est ouvert par le NAVIGATEUR : il ne peut pas poser
  // d'en-tête, et le seul credential qu'un `new WebSocket()` transporte est le
  // cookie de l'origine. C'est exactement ce que `_check_ws_auth` attend
  // (`websocket.cookies.get("api_key")`), et ce que posait `_tpl()` avant la
  // suppression de Jinja2 — depuis, plus personne.
  //
  // On le repose ici, sur l'origine Next, la même que celle du `/ws`
  // same-origin (rewrite en dev, nginx en prod). `HttpOnly` : le bundle client
  // ne peut pas le lire, la clé ne fuit pas dans le JS. `SameSite=Lax` : pas
  // d'envoi sur une navigation tierce.
  if (API_KEY) {
    // S-04 : derrière un proxy qui termine TLS, Next voit du HTTP interne.
    // Honorer x-forwarded-proto (premier hop) en plus du protocole local.
    const forwardedProto = req.headers
      .get('x-forwarded-proto')
      ?.split(',')[0]
      ?.trim()
      .toLowerCase();
    const secure =
      forwardedProto === 'https' || req.nextUrl.protocol === 'https:' ? '; Secure' : '';
    outHeaders.append(
      'set-cookie',
      `api_key=${encodeURIComponent(API_KEY)}; Path=/; HttpOnly; SameSite=Lax${secure}`,
    );
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: outHeaders,
  });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function PUT(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function PATCH(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
