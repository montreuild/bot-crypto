/**
 * Système d'internationalisation simple (FR/EN) via React Context.
 */

'use client';

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';

export type Locale = 'fr' | 'en';

const STORAGE_KEY = 'crypto-bot-locale';

const translations: Record<Locale, Record<string, string>> = {
  fr: {
    'nav.trading': 'Trading', 'nav.research': 'Recherche', 'nav.data': 'Données',
    'nav.config': 'Configuration', 'nav.dashboard': 'Dashboard', 'nav.lab': 'Laboratoire',
    'nav.market': 'Marché', 'nav.bots': 'Mes Bots',
    'nav.trades': 'Trades', 'nav.portfolio': 'Portefeuille', 'nav.backtest': 'Backtest',
    'nav.scanner': 'Scanner', 'nav.replay': 'Replay', 'nav.smartgraph': 'Smart Graph',
    'nav.smartreplay': 'Smart Replay', 'nav.compare': 'Comparatif', 'nav.optimizer': 'Optimiseur',
    'nav.audit': 'Audit OOS', 'nav.audit_log': 'Journal Audit', 'nav.derivatives': 'Dérivées',
    'nav.data_ohlcv': 'Bougies OHLCV', 'nav.models': 'Registre modèles',
    'nav.ml': 'Modèles ML', 'nav.settings': 'Réglages',
    'topbar.start': 'Démarrer', 'topbar.stop': 'Arrêter', 'topbar.search': 'Rechercher',
    'topbar.capital': 'Capital', 'topbar.pnl_total': 'PnL Total',
    'dashboard.title': 'Dashboard', 'dashboard.subtitle': 'Vue temps réel du trading',
    'common.loading': 'Chargement...', 'common.no_data': 'Aucune donnée',
    'common.refresh': 'Actualiser', 'common.export': 'Export CSV', 'common.cancel': 'Annuler',
    'settings.title': 'Réglages', 'settings.theme': 'Thème',
    'settings.theme_dark': 'Sombre', 'settings.theme_light': 'Clair',
    'settings.notifications': 'Notifications navigateur', 'settings.language': 'Langue',
  },
  en: {
    'nav.trading': 'Trading', 'nav.research': 'Research', 'nav.data': 'Data',
    'nav.config': 'Configuration', 'nav.dashboard': 'Dashboard', 'nav.lab': 'Lab',
    'nav.market': 'Market', 'nav.bots': 'My Bots',
    'nav.trades': 'Trades', 'nav.portfolio': 'Portfolio', 'nav.backtest': 'Backtest',
    'nav.scanner': 'Scanner', 'nav.replay': 'Replay', 'nav.smartgraph': 'Smart Graph',
    'nav.smartreplay': 'Smart Replay', 'nav.compare': 'Compare', 'nav.optimizer': 'Optimizer',
    'nav.audit': 'Audit OOS', 'nav.audit_log': 'Audit Log', 'nav.derivatives': 'Derivatives',
    'nav.data_ohlcv': 'OHLCV Data', 'nav.models': 'Model registry',
    'nav.ml': 'ML Models', 'nav.settings': 'Settings',
    'topbar.start': 'Start', 'topbar.stop': 'Stop', 'topbar.search': 'Search',
    'topbar.capital': 'Capital', 'topbar.pnl_total': 'Total PnL',
    'dashboard.title': 'Dashboard', 'dashboard.subtitle': 'Real-time trading view',
    'common.loading': 'Loading...', 'common.no_data': 'No data',
    'common.refresh': 'Refresh', 'common.export': 'Export CSV', 'common.cancel': 'Cancel',
    'settings.title': 'Settings', 'settings.theme': 'Theme',
    'settings.theme_dark': 'Dark', 'settings.theme_light': 'Light',
    'settings.notifications': 'Browser notifications', 'settings.language': 'Language',
  },
};

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>('fr');

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY) as Locale | null;
    if (stored === 'fr' || stored === 'en') {
      setLocaleState(stored);
    }
    // Pas de détection auto via navigator.language : l'UI est rédigée en
    // français ; un runner CI en en-US basculerait sinon nav/a11y/visuel.
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = (newLocale: Locale) => {
    setLocaleState(newLocale);
    localStorage.setItem(STORAGE_KEY, newLocale);
    document.documentElement.lang = newLocale;
  };

  const t = (key: string): string => {
    return translations[locale]?.[key] || translations.fr[key] || key;
  };

  return (
    <I18nContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    return {
      locale: 'fr' as Locale,
      setLocale: () => {},
      t: (key: string) => key,
    };
  }
  return ctx;
}
