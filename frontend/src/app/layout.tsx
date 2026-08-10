import type { Metadata, Viewport } from 'next';
import { Inter, JetBrains_Mono } from 'next/font/google';
import './globals.css';
import { Providers } from '@/components/providers';
import { Sidebar } from '@/components/layout/sidebar';
import { Topbar } from '@/components/layout/topbar';
import { Toaster } from '@/components/ui/toaster';
import { ApiStatusBanner } from '@/components/layout/api-status-banner';
import { NotificationPermissionProvider } from '@/components/notifications-provider';

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-jetbrains',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'Crypto Bot — Trading Algorithmique',
  description: 'Dashboard temps réel pour bot de trading crypto multi-stratégies',
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'Crypto Bot',
  },
  icons: {
    icon: [
      { url: 'data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 100 100%27%3E%3Ctext y=%27.9em%27 font-size=%2790%27%3E%E2%9A%A1%3C/text%3E%3C/svg%3E' },
    ],
    apple: '/icon-192.png',
  },
};

export const viewport: Viewport = {
  themeColor: '#f8fafc',
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
};

// Script inline pour appliquer le thème avant le render (évite le flash)
// P0-5 : la palette claire est la valeur par défaut (si pas de préférence stockée).
const themeScript = `
(function() {
  try {
    var stored = localStorage.getItem('theme');
    var theme = stored || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    if (theme === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.classList.remove('light');
    } else {
      document.documentElement.classList.add('light');
      document.documentElement.classList.remove('dark');
    }
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Pas de classe `dark` figée sur <html> : le script inline + setStoredTheme
  // gèrent light/dark. Une classe React en dur réappliquait `dark` au re-render.
  return (
    <html lang="fr" className={`${inter.variable} ${jetbrainsMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="font-sans antialiased min-h-screen bg-background text-foreground">
        <Providers>
          <NotificationPermissionProvider>
            {/* S2-F3-US5 — Skip-to-content link pour l'accessibilité clavier */}
            <a
              href="#main-content"
              className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:px-4 focus:py-2 focus:bg-primary-500 focus:text-background focus:rounded-md focus:shadow-lg"
            >
              Aller au contenu principal
            </a>
            <div className="flex h-screen overflow-hidden">
              <Sidebar />
              <div className="flex-1 flex flex-col overflow-hidden">
                <Topbar />
                <ApiStatusBanner />
                {/* `tabIndex={0}` : `<main>` défile (`overflow-y-auto`) et doit
                    donc être atteignable au clavier — sinon son contenu est
                    inaccessible à la molette près quand la page ne contient
                    aucun élément focusable (axe : scrollable-region-focusable,
                    relevé sur /ml). Il est par ailleurs la cible du skip-link,
                    ce qui rend le point de focus cohérent. */}
                <main id="main-content" tabIndex={0} className="flex-1 overflow-y-auto p-4 md:p-6">
                  {children}
                </main>
              </div>
            </div>
            <Toaster />
          </NotificationPermissionProvider>
        </Providers>
      </body>
    </html>
  );
}
