'use client';

/**
 * Provider qui demande la permission pour les notifications navigateur
 * et pousse des notifications natives pour les events WebSocket critiques.
 *
 * - Au mount : ne demande PAS la permission (l'utilisateur doit le faire depuis /settings).
 * - Si la permission est accordée : subscribe aux events WS de type risk.* (severity critical)
 *   et trade.closed (pnl négatif), et push une Notification native.
 * - Fonctionne même sur un autre onglet (les notifications natives ne nécessitent pas le focus).
 */

import { useEffect, ReactNode } from 'react';
import { useWebSocket } from '@/lib/ws-provider';
import type { WSEvent, RiskData, TradeClosedData } from '@/types';

const STORAGE_KEY = 'crypto-bot-notifications-enabled';

export function NotificationPermissionProvider({ children }: { children: ReactNode }) {
  const { subscribe } = useWebSocket();

  useEffect(() => {
    // Vérifie si l'utilisateur a activé les notifications (depuis /settings)
    const enabled = localStorage.getItem(STORAGE_KEY) === 'true';
    if (!enabled) return;

    if (typeof Notification === 'undefined') return;
    if (Notification.permission !== 'granted') return;

    const unsub = subscribe((event: WSEvent) => {
      // Notifications natives seulement pour les events critiques
      if (event.type.startsWith('risk.')) {
        const data = event.data as RiskData;
        if (data.severity === 'critical') {
          new Notification('🚨 Alerte critique — Crypto Bot', {
            body: `${event.type.replace('risk.', '')}: ${JSON.stringify(data).slice(0, 200)}`,
            icon: '/icon-192.png',
            tag: event.type,
            requireInteraction: true,
          });
        }
      } else if (event.type === 'trade.closed') {
        const data = event.data as TradeClosedData;
        if (data.pnl < 0) {
          new Notification('❌ Trade fermé en perte', {
            body: `${data.symbol} ${data.side.toUpperCase()} : ${data.pnl.toFixed(2)} USDC (${data.pnl_pct.toFixed(2)}%)`,
            icon: '/icon-192.png',
            tag: `trade-${data.slot_key}`,
          });
        } else if (data.pnl > 0) {
          new Notification('✅ Trade fermé en gain', {
            body: `${data.symbol} ${data.side.toUpperCase()} : +${data.pnl.toFixed(2)} USDC (+${data.pnl_pct.toFixed(2)}%)`,
            icon: '/icon-192.png',
            tag: `trade-${data.slot_key}`,
          });
        }
      }
    });

    return unsub;
  }, [subscribe]);

  return <>{children}</>;
}

// ── Helpers exposés pour /settings ──────────────────────────────────────────

export async function enableBrowserNotifications(): Promise<boolean> {
  if (typeof Notification === 'undefined') return false;
  if (Notification.permission === 'granted') {
    localStorage.setItem(STORAGE_KEY, 'true');
    return true;
  }
  if (Notification.permission === 'denied') return false;
  const permission = await Notification.requestPermission();
  if (permission === 'granted') {
    localStorage.setItem(STORAGE_KEY, 'true');
    // Notification de bienvenue
    new Notification('Notifications activées', {
      body: 'Vous recevrez les alertes critiques du bot en temps réel.',
      icon: '/icon-192.png',
    });
    return true;
  }
  return false;
}

export function disableBrowserNotifications() {
  localStorage.removeItem(STORAGE_KEY);
}

export function isBrowserNotificationsEnabled(): boolean {
  if (typeof Notification === 'undefined') return false;
  return localStorage.getItem(STORAGE_KEY) === 'true' && Notification.permission === 'granted';
}

export function getBrowserNotificationsPermission(): 'granted' | 'denied' | 'default' | 'unsupported' {
  if (typeof Notification === 'undefined') return 'unsupported';
  return Notification.permission;
}
