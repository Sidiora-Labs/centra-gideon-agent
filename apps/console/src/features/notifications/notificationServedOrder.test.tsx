import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { NotificationItem } from '../../shared/data/api';
const { notifications } = vi.hoisted(() => ({ notifications: vi.fn() }));
vi.mock('../../shared/data/api', async importOriginal => ({
  ...await importOriginal<Record<string, unknown>>(),
  api: { ...(await importOriginal<typeof import('../../shared/data/api')>()).api, notifications },
}));
vi.mock('../../shared/data/useChatSocket', () => ({ useChatSocket: () => {} }));
vi.mock('../../shared/data/rungs', () => ({ useAutonomyLadder: () => ({ ladder: null, refresh: () => {} }) }));
import { NotificationBell } from '../../shared/ui/NotificationBell';
import { NotificationsPage } from './NotificationsPage';
import { RecentSection } from '../companion/CompanionSections';
import { invalidateKeys } from '../../shared/data/data';
const titles = [9, 8, 7, 6, 5, 4, 3, 2, 1].map(n => `Note ${n}`);
const feed = (): NotificationItem[] => titles.map((title, i) => ({ kind: 'info', title, body: 'A recorded notification', ts: `2026-08-26T0${8-i}:00:00+00:00`, acked: false }));
async function shown() { return (await screen.findAllByText(/^Note \d$/)).map(node => node.textContent); }
beforeEach(() => {
  sessionStorage.clear();
  invalidateKeys('notifications');
  invalidateKeys('notifications-companion');
  notifications.mockResolvedValue({ notifications: feed(), unread: 9 });
});
afterEach(cleanup);
describe('served notification order', () => {
  it('shows the newest served rows first in the bell', async () => {
    render(<NotificationBell navigate={() => {}} />);
    fireEvent.click(await screen.findByRole('button', { name: /^Notifications, 9 unread$/ }));
    expect(await shown()).toEqual(titles.slice(0, 5));
  });
  it('preserves the served order in the full feed', async () => {
    render(<NotificationsPage query={{}} setQuery={() => {}} navigate={() => {}} />);
    expect(await shown()).toEqual(titles);
  });
  it('preserves the served order in the recent companion section', async () => {
    render(<RecentSection />);
    expect(await shown()).toEqual(titles.slice(0, 6));
  });
});
