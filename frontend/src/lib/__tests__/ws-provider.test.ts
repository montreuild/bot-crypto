import { describe, it, expect } from 'vitest';
import { wsUrlWithTicket } from '@/lib/ws-provider';

describe('wsUrlWithTicket', () => {
  it('laisse l’URL intacte sans ticket', () => {
    expect(wsUrlWithTicket('ws://localhost:3000/ws', null)).toBe('ws://localhost:3000/ws');
  });

  it('ajoute le ticket en query string', () => {
    const out = wsUrlWithTicket('ws://localhost:3000/ws', 'abc+1');
    expect(out).toContain('ticket=');
    expect(out).toContain('abc');
  });
});
