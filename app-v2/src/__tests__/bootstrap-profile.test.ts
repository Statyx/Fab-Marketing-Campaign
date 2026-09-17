import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const services = vi.hoisted(() => ({
  msal: vi.fn(() => ({ kind: 'msal' })),
  local: vi.fn(() => ({ kind: 'local' })),
  rayfin: vi.fn(() => ({ kind: 'rayfin' })),
  init: vi.fn(() => ({ client: true })),
  bridge: vi.fn(async () => undefined),
}));

vi.mock('@/services/msal', () => ({ msalConfigured: true }));
vi.mock('@/services/MsalAuthService', () => ({ MsalAuthService: services.msal }));
vi.mock('@/services/MockAuthService', () => ({ MockAuthService: services.local }));
vi.mock('@/services/RayfinAuthService', () => ({ RayfinAuthService: services.rayfin }));
vi.mock('@/services/rayfinClient', () => ({ initRayfinClient: services.init }));
vi.mock('@azure/msal-browser/redirect-bridge', () => ({
  broadcastResponseToMainFrame: services.bridge,
}));

import { bootstrapAuth } from '@/services/bootstrap';

beforeEach(() => {
  vi.clearAllMocks();
  for (const key of ['VITE_FABRIC_WORKSPACE_ID', 'VITE_FABRIC_ITEM_ID', 'VITE_FABRIC_PORTAL_URL']) {
    vi.stubEnv(key, '');
  }
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('existing MSAL bootstrap contract', () => {
  it.each(['http://localhost:5168', 'https://app.example.invalid'])(
    'chooses MSAL before either Rayfin session implementation with %s', (apiUrl) => {
      vi.stubEnv('VITE_RAYFIN_API_URL', apiUrl);
      vi.stubEnv('VITE_RAYFIN_PUBLISHABLE_KEY', 'pk-fixture');
      expect(bootstrapAuth()).toEqual({ kind: 'msal' });
      expect(services.msal).toHaveBeenCalledOnce();
      expect(services.local).not.toHaveBeenCalled();
      expect(services.rayfin).not.toHaveBeenCalled();
    });

  it('executes the dedicated landing-page bridge without starting an auth service', async () => {
    await import('@/redirect');
    expect(services.bridge).toHaveBeenCalledOnce();
    expect(services.init).not.toHaveBeenCalled();
    expect(services.msal).not.toHaveBeenCalled();
  });
});
