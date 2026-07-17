/** @type {import('next').NextConfig} */
const nextConfig = {
  // Le backend FastAPI tourne sur http://localhost:8000
  // On redirige /api/* et /ws vers le backend pour éviter les problèmes CORS
  async rewrites() {
    return [
      { source: '/api/:path*', destination: 'http://localhost:8000/api/:path*' },
      { source: '/health', destination: 'http://localhost:8000/health' },
    ];
  },
  // WebSocket : Next.js ne proxy pas les WS nativement, on laisse le client
  // se connecter directement à ws://localhost:8000/ws (configurable via NEXT_PUBLIC_WS_URL)
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
    NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws',
    NEXT_PUBLIC_API_KEY: process.env.NEXT_PUBLIC_API_KEY || '',
  },
};

export default nextConfig;
