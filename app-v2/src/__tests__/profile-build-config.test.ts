// @vitest-environment node
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { APP_ROOT } from '../../scripts/deployment-profile.mjs';

const selected = vi.hoisted(() => ({
  envDir: '',
  buildDir: '',
  expectedEnv: { VITE_PORT: '5187' },
}));
vi.mock('../../scripts/deployment-profile.mjs', async (original) => ({
  ...(await original<object>()),
  appProfile: vi.fn(() => selected),
  validateViteEnv: vi.fn(),
  checkReceipts: vi.fn(),
  profileCapturePlugin: vi.fn(() => ({ name: 'mock-profile-capture' })),
  readRegistry: vi.fn(() => ({ deployments: {} })),
}));
vi.mock('vite', async (original) => ({
  ...(await original<object>()),
  loadEnv: vi.fn(() => selected.expectedEnv),
}));
vi.mock('@vitejs/plugin-react-swc', () => ({ default: () => ({ name: 'mock-react' }) }));
vi.mock('@tailwindcss/vite', () => ({ default: () => ({ name: 'mock-tailwind' }) }));

import config from '../../vite.config';
import { loadEnv } from 'vite';
import { validateViteEnv, checkReceipts, profileCapturePlugin } from '../../scripts/deployment-profile.mjs';

describe('profile-aware Vite configuration', () => {
  it('selects the isolated envDir and retains both built MSAL landing entries', () => {
    selected.envDir = join(APP_ROOT, 'fixture-profile', 'app');
    selected.buildDir = join(APP_ROOT, 'fixture-profile', 'artifacts', 'app-v2', 'dist');
    const result = config({ mode: 'production', command: 'build', isSsrBuild: false, isPreview: false });
    expect(result.envDir).toBe(selected.envDir);
    expect(loadEnv).toHaveBeenCalledWith('production', selected.envDir, 'VITE_');
    expect(validateViteEnv).toHaveBeenCalled();
    expect(checkReceipts).toHaveBeenCalled();
    expect(profileCapturePlugin).toHaveBeenCalledWith(selected);
    expect(result.plugins).toContainEqual({ name: 'mock-profile-capture' });
    expect(result.server).toEqual({ port: 5187, strictPort: true });
    expect(result.build?.outDir).toBe(selected.buildDir);
    expect(result.build?.emptyOutDir).toBe(true);
    expect(result.build?.rollupOptions?.input).toEqual({
      main: join(APP_ROOT, 'index.html'), blank: join(APP_ROOT, 'blank.html'),
    });
  });

  it('keeps origin/blank.html and the response-hash router guard', () => {
    const msal = readFileSync(join(APP_ROOT, 'src', 'services', 'msal.ts'), 'utf8');
    const main = readFileSync(join(APP_ROOT, 'src', 'main.tsx'), 'utf8');
    const blank = readFileSync(join(APP_ROOT, 'blank.html'), 'utf8');
    expect(msal).toContain('`${window.location.origin}/blank.html`');
    expect(main).toContain('/(^|[#&?])(code|error|state|id_token)=/');
    expect(main).toMatch(/if \(isAuthResponse\)[\s\S]*broadcastResponseToMainFrame[\s\S]*else/);
    expect(blank).toContain('/src/redirect.ts');
  });
});
