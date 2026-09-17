// @vitest-environment node
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir, tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { loadEnv } from 'vite';
import {
  APP_ROOT, PROFILE_ENV, appProfile, checkCompletedDeployment, checkReceipts, checkToken,
  planRayfin, profileCapturePlugin, profileDirectory, readRegistry, restoreRayfinTemplate, validateViteEnv, workspaceKey,
} from '../../scripts/deployment-profile.mjs';
import { acquireCliProfileToken, inspectProfileTarget, runRayfin } from '../../scripts/rayfin-profile.mjs';

const guid = (n: number) => `${n.toString().padStart(8, '0')}-0000-4000-8000-${'0'.repeat(12)}`;
const tenant = guid(1);
const workspace = guid(2);
const client = guid(3);
const model = guid(4);
const item = guid(5);
const account = 'operator@example.invalid';
const endpoint = 'https://new-foundry.example.invalid/api/projects/demo';

let root: string;
let appRoot: string;
let profile: string;
let appDir: string;
let cacheRoot: string;
let cacheDir: string;
let env: Record<string, string>;

function write(path: string, contents: string | object) {
  mkdirSync(resolve(path, '..'), { recursive: true });
  writeFileSync(path, typeof contents === 'string' ? contents : JSON.stringify(contents));
}

function modeEnv(overrides: Record<string, string> = {}) {
  return Object.entries({
    VITE_ENTRA_CLIENT_ID: client,
    VITE_ENTRA_TENANT_ID: tenant,
    VITE_DATA_WORKSPACE_ID: workspace,
    VITE_SEMANTIC_MODEL_ID: model,
    VITE_FOUNDRY_ENDPOINT: endpoint,
    VITE_FOUNDRY_SUPERVISOR: 'Marketing-Supervisor',
    VITE_FOUNDRY_MODEL: 'demo-model',
    ...overrides,
  }).map(([key, value]) => `${key}=${value}`).join('\n');
}

function record(overrides: Record<string, string> = {}) {
  return {
    fabricWorkspaceId: workspace, fabricTenantId: tenant, fabricItemId: item,
    fabricApiUrl: 'https://new-app.example.invalid', publishableKey: 'pk-fixture',
    ...overrides,
  };
}

function registry(deployments: Record<string, object>, active = 'legacy') {
  write(join(appRoot, 'rayfin', '.deployments.json'), { active, deployments });
}

function auth(overrides: Record<string, string> = {}) {
  write(join(cacheDir, 'auth.json'), {
    identityType: 'user', tenantId: tenant, userPrincipalName: account, ...overrides,
  });
  write(join(cacheDir, 'cache.bin'), 'offline fixture, never passed to MSAL');
}

function configureCaches(values: Record<string, string | undefined>) {
  const path = join(profile, 'config.yaml');
  const config = JSON.parse(readFileSync(path, 'utf8'));
  Object.assign(config.deployment, values);
  write(path, config);
}

function jwt(overrides: Record<string, unknown> = {}) {
  const claims = { tid: tenant, preferred_username: account, aud: 'https://api.fabric.microsoft.com',
    exp: Math.floor(Date.now() / 1000) + 3600,
    ...overrides };
  return `header.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.signature`;
}

function capture() {
  const state = JSON.parse(readFileSync(join(profile, 'state.json'), 'utf8'));
  return {
    deployment: {
      tenantId: tenant, workspaceId: workspace, semanticModelId: model,
      dataAgentId: state.data_agent_id, projectEndpoint: endpoint,
      agentName: 'Marketing-Supervisor', model: 'demo-model',
    },
    answers: {
      'A fixture question': {
        text: 'A fixture answer', toolsFired: ['DataA2A'], seconds: 1,
        capturedAt: '2026-01-01T00:00:00Z',
      },
    },
  };
}

beforeAll(() => {
  // Cold module loading on synchronized paths must not consume an assertion's timeout.
  const require = createRequire(import.meta.url);
  createRequire(require.resolve('@microsoft/rayfin-cli/package.json'))('yaml');
}, 60_000);

beforeEach(() => {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith('VITE_')) vi.stubEnv(key, undefined);
  }
  vi.stubGlobal('fetch', vi.fn(() => { throw new Error('No cloud requests in offline tests'); }));
  root = mkdtempSync(join(tmpdir(), 'marketing-app-profile-'));
  cacheRoot = mkdtempSync(join(tmpdir(), 'marketing-rayfin-cache-'));
  cacheDir = join(cacheRoot, 'tenant-profile', '.rayfin');
  appRoot = join(root, 'app-v2');
  profile = join(root, 'deployments', 'next');
  appDir = join(profile, 'app');
  env = { [PROFILE_ENV]: profile };
  write(join(profile, 'config.yaml'), {
    tenant_id: tenant, az_subscription: guid(6), workspace_name: 'Marketing Demo',
    deployment: {
      expected_account: account,
      azure_config_dir: join(dirname(cacheDir), '.azure'),
      rayfin_config_dir: cacheDir,
    },
    foundry: { project_endpoint: endpoint, model_deployment: 'demo-model',
      supervisor: { agent_name: 'Marketing-Supervisor' } },
  });
  write(join(profile, 'state.json'), {
    _deployment_context: { tenant_id: tenant, subscription_id: guid(6), account,
      workspace_name: 'Marketing Demo' },
    workspace_id: workspace, semantic_model_id: model,
    data_agent_id: guid(7),
    foundry_project_endpoint: endpoint,
    foundry_supervisor_agent_name: 'Marketing-Supervisor',
  });
  write(join(appDir, '.env.production.local'), modeEnv());
  write(join(appDir, '.env.development.local'), modeEnv({ VITE_PORT: '5179' }));
  write(join(appDir, '.env'), `RAYFIN_TENANT_ID=${tenant}\nRAYFIN_WORKSPACE_ID=${workspace}`);
  write(join(appRoot, 'rayfin', 'rayfin.yml'), {
    id: 'App-Customer-360', name: 'App-Customer-360',
    services: {
      data: { enabled: false }, storage: { enabled: false }, functions: { enabled: false },
      staticHosting: { enabled: true, folder: '${FAB_MARKETING_APP_DIST:-dist}' },
    },
  });
  write(join(appRoot, '.env.production.local'), modeEnv({
    VITE_ENTRA_CLIENT_ID: guid(30), VITE_FOUNDRY_ENDPOINT: 'https://legacy.example.invalid',
    VITE_ENTRA_TENANT_ID: guid(22), VITE_DATA_WORKSPACE_ID: guid(20), VITE_SEMANTIC_MODEL_ID: guid(21),
  }));
  write(join(appRoot, '.env.local'), 'VITE_RAYFIN_API_URL=https://legacy-app.example.invalid');
  write(join(root, 'src', 'state.json'), { workspace_id: guid(20), semantic_model_id: guid(21) });
  auth();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  rmSync(root, { recursive: true, force: true });
  rmSync(cacheRoot, { recursive: true, force: true });
});

describe('shared profile selection', () => {
  it('keeps the original env/up commands with no selector or pointer', () => {
    expect(profileDirectory({ appRoot, env: {} })).toBeNull();
    expect(planRayfin('env', [], { appRoot, env: {} }).args).toEqual(['env', '--framework', 'vite']);
    expect(planRayfin('up', ['--exclude-services', 'staticHosting'], { appRoot, env: {} }).args)
      .toEqual(['up', '--exclude-services', 'staticHosting']);
  });

  it('resolves the pointer under repository deployments, independent of cwd', () => {
    write(join(root, 'deployments', 'active-profile.json'), { profile: 'next' });
    expect(profileDirectory({ appRoot, env: {} })).toBe(profile);
  });

  it('gives the env selector precedence over even a malformed pointer', () => {
    write(join(root, 'deployments', 'active-profile.json'), 'not json');
    expect(profileDirectory({ appRoot, env: { [PROFILE_ENV]: join('deployments', 'next') } }))
      .toBe(profile);
  });

  it.each(['', '.', '..', '../next', 'nested\\next'])('rejects invalid pointer %j', (name) => {
    write(join(root, 'deployments', 'active-profile.json'), { profile: name });
    expect(() => profileDirectory({ appRoot, env: {} })).toThrow('named deployment profile');
  });

  it('does not treat an explicitly empty selector as no selection', () => {
    expect(() => profileDirectory({ appRoot, env: { [PROFILE_ENV]: '' } })).toThrow(PROFILE_ENV);
  });

  it('fails on a missing selected config instead of using src/config.yaml', () => {
    rmSync(join(profile, 'config.yaml'));
    write(join(root, 'src', 'config.yaml'), 'legacy: true');
    expect(() => appProfile('production', { appRoot, env })).toThrow('no separate config.yaml');
  });

  it.each(['[]', '[broken'])('rejects invalid selected config content %s', (content) => {
    write(join(profile, 'config.yaml'), content);
    expect(() => appProfile('production', { appRoot, env })).toThrow();
  });

  it('rejects src as a deployment profile', () => {
    write(join(root, 'src', 'config.yaml'), 'legacy: true');
    expect(() => profileDirectory({ appRoot, env: { [PROFILE_ENV]: 'src' } }))
      .toThrow('no separate config.yaml');
  });

  it('rejects unstamped receipts', () => {
    write(join(profile, 'state.json'), { workspace_id: workspace });
    expect(() => appProfile('production', { appRoot, env })).toThrow('does not belong');
  });
});

describe('isolated Vite input', () => {
  it('loads profile env rather than either legacy app env file', () => {
    const selected = appProfile('production', { appRoot, env })!;
    const actual = loadEnv('production', selected.envDir, 'VITE_');
    expect(selected.envDir).toBe(appDir);
    expect(actual.VITE_ENTRA_CLIENT_ID).toBe(client);
    expect(actual.VITE_RAYFIN_API_URL).toBeUndefined();
    expect(() => validateViteEnv(selected, actual)).not.toThrow();
  });

  it('requires the selected mode-local file even when the other mode and legacy files exist', () => {
    rmSync(join(appDir, '.env.production.local'));
    expect(() => appProfile('production', { appRoot, env })).toThrow();
    expect(appProfile('development', { appRoot, env })!.expectedEnv.VITE_PORT).toBe('5179');
  });

  it.each(['VITE_ENTRA_TENANT_ID', 'VITE_DATA_WORKSPACE_ID', 'VITE_SEMANTIC_MODEL_ID',
    'VITE_FOUNDRY_ENDPOINT', 'VITE_FOUNDRY_SUPERVISOR', 'VITE_FOUNDRY_MODEL'])(
    'refuses mismatched %s', (name) => {
      write(join(appDir, '.env.production.local'), modeEnv({ [name]: 'stale' }));
      expect(() => appProfile('production', { appRoot, env })).toThrow(name);
    });

  it('cannot enable opaque Rayfin auth by omitting MSAL registration', () => {
    write(join(appDir, '.env.production.local'), modeEnv({ VITE_ENTRA_CLIENT_ID: '' }));
    expect(() => appProfile('production', { appRoot, env })).toThrow('VITE_ENTRA_CLIENT_ID');
  });

  it('requires the persisted Foundry endpoint, not just configuration intent', () => {
    const path = join(profile, 'state.json');
    const state = JSON.parse(readFileSync(path, 'utf8'));
    delete state.foundry_project_endpoint;
    write(path, state);
    expect(() => appProfile('production', { appRoot, env })).toThrow('state.foundry_project_endpoint');
  });

  it('rejects disagreement between the Foundry endpoint receipt and configuration', () => {
    const path = join(profile, 'config.yaml');
    const config = JSON.parse(readFileSync(path, 'utf8'));
    config.foundry.project_endpoint = 'https://other-project.example.invalid';
    write(path, config);
    expect(() => appProfile('production', { appRoot, env })).toThrow('Foundry project endpoint receipt');
  });

  it('rejects copied legacy SPA IDs', () => {
    write(join(appDir, '.env.production.local'), modeEnv({ VITE_ENTRA_CLIENT_ID: guid(30) }));
    expect(() => appProfile('production', { appRoot, env })).toThrow('legacy VITE_ENTRA_CLIENT_ID');
  });

  it('cannot copy the old production SPA into the new development environment', () => {
    write(join(appDir, '.env.development.local'), modeEnv({ VITE_ENTRA_CLIENT_ID: guid(30) }));
    expect(() => appProfile('development', { appRoot, env })).toThrow('legacy VITE_ENTRA_CLIENT_ID');
  });

  it('rejects old workspace receipts even with a new context stamp', () => {
    write(join(root, 'src', 'state.json'), { workspace_id: workspace });
    expect(() => appProfile('production', { appRoot, env })).toThrow('legacy state.workspace_id');
  });

  it('rejects inherited values absent from the profile as well as conflicting ones', () => {
    for (const inherited of [
      { VITE_DATA_WORKSPACE_ID: guid(30) },
      { VITE_RAYFIN_API_URL: 'https://legacy-app.example.invalid' },
    ]) {
      expect(() => appProfile('production', { appRoot, env: { ...env, ...inherited } }))
        .toThrow('Inherited VITE_');
    }
  });

  it('allows matching explicit inherited values', () => {
    expect(appProfile('production', { appRoot,
      env: { ...env, VITE_ENTRA_TENANT_ID: tenant } })).not.toBeNull();
  });

  it('rejects base-file additions after Vite resolves the environment', () => {
    write(join(appDir, '.env.local'), 'VITE_RAYFIN_API_URL=https://stale.example.invalid');
    const selected = appProfile('production', { appRoot, env })!;
    expect(() => validateViteEnv(selected, loadEnv('production', selected.envDir, 'VITE_')))
      .toThrow('mode-local profile file');
  });

  it('rejects Vite shell precedence even when the selected file is otherwise valid', () => {
    const selected = appProfile('production', { appRoot, env })!;
    vi.stubEnv('VITE_FOUNDRY_ENDPOINT', 'https://other.example.invalid');
    expect(() => validateViteEnv(selected, loadEnv('production', selected.envDir, 'VITE_')))
      .toThrow('mode-local profile file');
  });
});

describe('external Rayfin cache placement', () => {
  it('prefers the configured external cache without creating it', () => {
    const alternate = join(cacheRoot, 'other-approved-cache', '.rayfin');
    configureCaches({ rayfin_config_dir: alternate });
    const plan = planRayfin('up', ['--dry-run'], { appRoot, env });
    expect(plan.cacheDir).toBe(alternate);
    expect(plan.env.RAYFIN_CONFIG_DIR).toBe(alternate);
    expect(existsSync(alternate)).toBe(false);
    expect(existsSync(join(appDir, '.rayfin'))).toBe(false);
  });

  it('derives a sibling of the isolated Azure CLI cache when no explicit path is configured', () => {
    configureCaches({ rayfin_config_dir: undefined });
    expect(planRayfin('up', [], { appRoot, env }).env.RAYFIN_CONFIG_DIR).toBe(cacheDir);
  });

  it('expands a private cache variable and accepts only a matching inherited selection', () => {
    configureCaches({ rayfin_config_dir: join('%CACHE_BASE%', 'tenant-profile', '.rayfin') });
    const plan = planRayfin('up', [], {
      appRoot, env: { ...env, CACHE_BASE: cacheRoot, RAYFIN_CONFIG_DIR: cacheDir },
    });
    expect(plan.cacheDir).toBe(cacheDir);
  });

  it.each(['', 'relative-cache'])('rejects invalid configured cache path %j', (value) => {
    configureCaches({ rayfin_config_dir: value });
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('deployment.rayfin_config_dir');
  });

  it('does not fall back to a global cache when both configuration fields are absent', () => {
    configureCaches({ rayfin_config_dir: undefined, azure_config_dir: undefined });
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('Configure deployment.rayfin_config_dir');
  });

  it.each(['.rayfin', '.azure'])('rejects the normal home %s cache without reading credentials', (name) => {
    configureCaches({ rayfin_config_dir: join(homedir(), name) });
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('normal/global caches');
  });

  it('rejects profile-local and other repository-local cache locations', () => {
    for (const path of [join(appDir, '.rayfin'), join(root, '.rayfin')]) {
      configureCaches({ rayfin_config_dir: path });
      expect(() => planRayfin('up', [], { appRoot, env })).toThrow('outside the repository');
      expect(existsSync(path)).toBe(false);
    }
  });

  it('rejects OneDrive paths even when no OneDrive environment variable is set', () => {
    configureCaches({ rayfin_config_dir: join(cacheRoot, 'OneDrive - Fixture', '.rayfin') });
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('OneDrive');
  });

  it('also rejects a custom OneDrive root named by the environment', () => {
    const synced = join(cacheRoot, 'synced-fixture');
    configureCaches({ rayfin_config_dir: join(synced, '.rayfin') });
    expect(() => planRayfin('up', [], { appRoot, env: { ...env, OneDriveCommercial: synced } }))
      .toThrow('OneDrive');
  });

  it('rejects an external junction that resolves back into the repository', () => {
    const link = join(cacheRoot, 'linked-cache');
    symlinkSync(appDir, link, 'junction');
    configureCaches({ rayfin_config_dir: link });
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('outside the repository');
  });

  it('also rejects a profile-local junction pointing to an external cache', () => {
    const link = join(appDir, '.rayfin');
    symlinkSync(cacheDir, link, 'junction');
    configureCaches({ rayfin_config_dir: link });
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('outside the repository');
  });

  it('rejects overlapping Azure and Rayfin cache directories', () => {
    const azure = join(dirname(cacheDir), '.azure');
    for (const path of [azure, join(azure, '.rayfin'), dirname(azure)]) {
      configureCaches({ rayfin_config_dir: path });
      expect(() => planRayfin('up', [], { appRoot, env })).toThrow('non-overlapping directories');
    }
  });
});

describe('supported CLI targeting and receipt provenance', () => {
  it('ignores an old active entry and passes explicit tenant, workspace and metadata', () => {
    registry({ legacy: record({ fabricWorkspaceId: guid(20), fabricTenantId: guid(21) }) });
    const before = readFileSync(join(appRoot, 'rayfin', '.deployments.json'), 'utf8');
    const plan = planRayfin('up', [], { appRoot, env });
    expect(plan.args).toEqual(['up', '--tenant', tenant, '--workspace-id', workspace,
      '--env-file', join(appDir, '.env')]);
    expect(plan.env.RAYFIN_CONFIG_DIR).toBe(cacheDir);
    expect(existsSync(join(appDir, '.rayfin'))).toBe(false);
    expect(plan.env.RAYFIN_ENV_FILE).toBe(join(appDir, '.env'));
    expect(plan.env.FAB_MARKETING_APP_DIST).toBe(join(profile, 'artifacts', 'app-v2', 'dist'));
    expect(plan.env[PROFILE_ENV]).toBe(profile);
    expect(readFileSync(join(appRoot, 'rayfin', '.deployments.json'), 'utf8')).toBe(before);
  });

  it('uses the installed CLI interpolation for the same isolated build output', async () => {
    const { parseRayfinYamlInterpolated } = await import('@microsoft/rayfin-tools-common/_internal/config');
    const source = readFileSync(join(APP_ROOT, 'rayfin', 'rayfin.yml'), 'utf8');
    const plan = planRayfin('up', [], { appRoot, env });
    const config = parseRayfinYamlInterpolated(source, new Map(Object.entries(plan.env)));
    expect(config.services.staticHosting?.folder).toBe(join(profile, 'artifacts', 'app-v2', 'dist'));
    expect(parseRayfinYamlInterpolated(source, new Map()).services.staticHosting?.folder).toBe('dist');
    expect(existsSync(plan.env.FAB_MARKETING_APP_DIST)).toBe(false);
  });

  it('rejects a requested legacy build output directory', () => {
    expect(() => planRayfin('up', [], { appRoot,
      env: { ...env, FAB_MARKETING_APP_DIST: join(appRoot, 'dist') } }))
      .toThrow('Inherited FAB_MARKETING_APP_DIST');
  });

  it('preserves the CLI sanitizer contract for registry keys', () => {
    expect(workspaceKey('  Marketing__Demo! -- Sweden  ')).toBe('marketing-demo-sweden');
  });

  it.each([
    ['--tenant', 'old'], ['--workspace-id', 'old'], ['--workspace', 'old'],
    ['--env-file', 'old'], ['--workspace-uri', 'old'], ['--force'],
    ['staticapp', 'deploy'], ['--exclude-services', 'functions'],
  ])('rejects profile targeting bypass %j', (...args) => {
    expect(() => planRayfin('up', args, { appRoot, env })).toThrow('Unsupported profile argument');
  });

  it.each(['fabricWorkspaceId', 'fabricTenantId'])('rejects a same-name legacy %s', (field) => {
    registry({ 'marketing-demo': record({ [field]: guid(30) }) });
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('Rayfin receipt');
  });

  it('rejects an ID-matching receipt under another name with a different tenant', () => {
    registry({ oldname: record({ fabricTenantId: guid(30) }) });
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('Rayfin receipt oldname');
  });

  it('rejects malformed metadata rather than letting the CLI start an empty registry', () => {
    write(join(appRoot, 'rayfin', '.deployments.json'), 'not json');
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow();
  });

  it('rejects old optional hosting env even when the data-plane bindings are current', () => {
    write(join(appDir, '.env.production.local'), modeEnv({ VITE_FABRIC_ITEM_ID: guid(30) }));
    registry({ 'marketing-demo': record() });
    const selected = appProfile('production', { appRoot, env })!;
    expect(() => checkReceipts(selected, readRegistry(appRoot))).toThrow('VITE_FABRIC_ITEM_ID');
  });

  it.each(['identityType', 'tenantId', 'userPrincipalName'])('rejects foreign Rayfin auth %s', (field) => {
    auth({ [field]: 'wrong' });
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('Rayfin');
  });

  it('never falls back to the normal Rayfin cache when the profile cache is missing', () => {
    rmSync(join(cacheDir, 'auth.json'));
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow();
  });

  it.each(['RAYFIN_TOKEN', 'RAYFIN_CONFIG_DIR', 'RAYFIN_WORKSPACE_ID', 'RAYFIN_AUTHORITY_HOST'])(
    'rejects inherited %s that could bypass isolation', (name) => {
      expect(() => planRayfin('up', [], { appRoot, env: { ...env, [name]: 'old' } }))
        .toThrow(`Inherited ${name}`);
    });

  it('rejects ambient tokens in the selected metadata file too', () => {
    write(join(appDir, '.env'), `RAYFIN_TENANT_ID=${tenant}\nRAYFIN_WORKSPACE_ID=${workspace}\nRAYFIN_TOKEN=old`);
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('RAYFIN_TOKEN');
  });

  it('keeps data, storage and functions disabled', () => {
    const path = join(appRoot, 'rayfin', 'rayfin.yml');
    const config = JSON.parse(readFileSync(path, 'utf8'));
    config.services.data.enabled = true;
    write(path, config);
    expect(() => planRayfin('up', [], { appRoot, env })).toThrow('data.enabled');
  });

  it('retains old receipts when the new workspace becomes active', () => {
    const legacy = record({ fabricWorkspaceId: guid(20), fabricTenantId: guid(21) });
    registry({ legacy });
    const plan = planRayfin('up', [], { appRoot, env });
    registry({ legacy, 'marketing-demo': record() }, 'marketing-demo');
    expect(() => checkCompletedDeployment(plan)).not.toThrow();
    registry({ 'marketing-demo': record() }, 'marketing-demo');
    expect(() => checkCompletedDeployment(plan)).toThrow('unrelated deployment receipt');
  });

  it('refuses a renamed live workspace before its name can select another tenant receipt', async () => {
    const plan = planRayfin('up', [], { appRoot, env });
    const request = vi.fn(async () => ({
      ok: true, json: async () => ({ id: workspace, displayName: 'Legacy workspace' }),
    }));
    await expect(inspectProfileTarget(plan, jwt(), request)).rejects.toThrow('Live workspace differs');
    expect(request).toHaveBeenCalledOnce();
  });

  it('checks a recorded item as well as the workspace before permitting reuse', async () => {
    registry({ 'marketing-demo': record() });
    const plan = planRayfin('up', [], { appRoot, env });
    const request = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: workspace, displayName: 'Marketing Demo' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: item, type: 'SQLDatabase',
        displayName: 'App-Customer-360' }) });
    await expect(inspectProfileTarget(plan, jwt(), request)).rejects.toThrow('expected App-Customer-360');
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('does not call success an unchanged old active receipt', () => {
    const plan = planRayfin('up', [], { appRoot, env });
    registry({ legacy: record({ fabricWorkspaceId: guid(20) }) });
    expect(() => checkCompletedDeployment(plan)).toThrow('did not persist');
  });

  it('restores only the CLI-resolved folder and newly hosted redirect in the tracked template', () => {
    const configPath = join(appRoot, 'rayfin', 'rayfin.yml');
    const template = JSON.parse(readFileSync(configPath, 'utf8'));
    template.services.auth = { allowedRedirectUris: ['http://localhost:5173'] };
    write(configPath, template);
    const before = readFileSync(configPath, 'utf8');
    const plan = planRayfin('up', [], { appRoot, env });
    const hosted = 'https://new-app.example.invalid';
    template.services.staticHosting.folder = plan.profile.buildDir;
    template.services.auth.allowedRedirectUris.push(hosted);
    write(configPath, template);
    registry({ 'marketing-demo': record({ hostingUrl: hosted }) }, 'marketing-demo');
    restoreRayfinTemplate(plan);
    expect(readFileSync(configPath, 'utf8')).toBe(before);
  });

  it('does not erase another edit made during a CLI deployment', () => {
    const plan = planRayfin('up', [], { appRoot, env });
    const configPath = join(appRoot, 'rayfin', 'rayfin.yml');
    const changed = JSON.parse(readFileSync(configPath, 'utf8'));
    changed.description = 'A concurrent user edit';
    write(configPath, changed);
    expect(() => restoreRayfinTemplate(plan)).toThrow();
    expect(JSON.parse(readFileSync(configPath, 'utf8')).description).toBe(changed.description);
  });
});

describe('profile-specific frozen-answer input', () => {
  it('uses the existing live miss behavior with a warning, never the old capture', () => {
    const selected = appProfile('production', { appRoot, env })!;
    const plugin = profileCapturePlugin(selected);
    const warn = vi.fn();
    const loaded = plugin.load.call({ warn }, join(appRoot, 'src', 'data', 'frozen-answers.generated.json'));
    expect(JSON.parse(loaded)).toEqual({ answers: {} });
    const viteId = join(appRoot, 'src', 'data', 'frozen-answers.generated.json').replaceAll('\\', '/');
    expect(JSON.parse(plugin.load.call({ warn }, viteId))).toEqual({ answers: {} });
    expect(JSON.parse(plugin.load.call({ warn }, `${viteId}?import`))).toEqual({ answers: {} });
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('live supervisor'));
    expect(plugin.load.call({ warn }, join(appRoot, 'src', 'data', 'topology.generated.json'))).toBeNull();
  });

  it('loads only a matching capture and keeps private provenance outside the bundle', () => {
    const payload = capture();
    write(join(profile, 'artifacts', 'captures', 'frozen_answers.json'), payload);
    const selected = appProfile('production', { appRoot, env })!;
    const warn = vi.fn();
    const plugin = profileCapturePlugin(selected);
    const loaded = plugin.load.call({ warn }, join(appRoot, 'src', 'data', 'frozen-answers.generated.json'));
    expect(JSON.parse(loaded)).toEqual({ answers: payload.answers });
    expect(loaded).not.toContain(account);
    expect(loaded).not.toContain('projectEndpoint');
    expect(warn).not.toHaveBeenCalled();
    expect(selected.buildDir).toBe(join(profile, 'artifacts', 'app-v2', 'dist'));
    expect(selected.capturePath).not.toContain(selected.buildDir);
  });

  it('rejects an unstamped legacy capture copied into the selected artifacts', () => {
    write(join(profile, 'artifacts', 'captures', 'frozen_answers.json'), { answers: capture().answers });
    expect(() => appProfile('production', { appRoot, env })).toThrow('Frozen answers do not belong');
  });

  it.each(['tenantId', 'workspaceId', 'semanticModelId', 'dataAgentId', 'projectEndpoint', 'agentName', 'model'])(
    'rejects a capture with stale %s even within the same profile', (field) => {
      const payload = capture();
      payload.deployment[field] = 'old-binding';
      write(join(profile, 'artifacts', 'captures', 'frozen_answers.json'), payload);
      expect(() => appProfile('production', { appRoot, env })).toThrow('Frozen answers do not belong');
    });

  it('rejects malformed captures rather than silently switching to live mode', () => {
    write(join(profile, 'artifacts', 'captures', 'frozen_answers.json'), 'invalid json');
    expect(() => appProfile('production', { appRoot, env })).toThrow();
  });
});

describe('offline boundaries', () => {
  it('supports an explicit checked Azure CLI identity without forging Rayfin cache files', () => {
    configureCaches({ rayfin_auth_source: 'azure-cli' });
    rmSync(join(cacheDir, 'auth.json'));
    const plan = planRayfin('up', [], { appRoot, env });
    const token = jwt();
    const execute = vi.fn(() => ({ status: 0, stdout: token, stderr: '' }));
    expect(acquireCliProfileToken(plan, execute)).toBe(token);
    expect(execute).toHaveBeenCalledWith('python',
      ['-c', expect.stringContaining('get_fabric_token'), join(appRoot, '..', 'src')],
      expect.objectContaining({ env: plan.env, cwd: appRoot }));
    expect(existsSync(join(cacheDir, 'auth.json'))).toBe(false);
  });

  it('does not fall back to another identity when Azure CLI acquisition fails', () => {
    configureCaches({ rayfin_auth_source: 'azure-cli' });
    const plan = planRayfin('up', [], { appRoot, env });
    expect(() => acquireCliProfileToken(plan,
      () => ({ status: 1, stdout: '', stderr: 'profile identity mismatch' })))
      .toThrow('Checked Azure CLI token acquisition failed');
  });

  it('rejects an unsupported explicit authentication source', () => {
    configureCaches({ rayfin_auth_source: 'another-cache' });
    expect(() => appProfile('production', { appRoot, env })).toThrow('rayfin_auth_source');
  });

  it('checks the Fabric audience even when tenant and user are correct', () => {
    expect(() => checkToken(appProfile('production', { appRoot, env }),
      jwt({ aud: 'https://graph.microsoft.com' }))).toThrow('token audience');
  });

  it('checks receipts after Azure CLI deployment without requiring a Rayfin login', () => {
    configureCaches({ rayfin_auth_source: 'azure-cli' });
    rmSync(join(cacheDir, 'auth.json'));
    const plan = planRayfin('up', [], { appRoot, env });
    registry({ 'marketing-demo': record() }, 'marketing-demo');
    expect(() => checkCompletedDeployment(plan)).not.toThrow();
  });

  it('profile env preparation neither launches the CLI nor writes files', async () => {
    const execute = vi.fn();
    const acquireToken = vi.fn();
    const before = readFileSync(join(appRoot, '.env.local'), 'utf8');
    expect(await runRayfin('env', [], { appRoot, env, execute, acquireToken })).toBe(0);
    expect(execute).not.toHaveBeenCalled();
    expect(acquireToken).not.toHaveBeenCalled();
    expect(readFileSync(join(appRoot, '.env.local'), 'utf8')).toBe(before);
    expect(existsSync(join(appDir, '.env.local'))).toBe(false);
  });

  it('planning a dry-run needs no auth and changes no registry', () => {
    rmSync(join(cacheDir, 'auth.json'));
    const plan = planRayfin('up', ['--dry-run'], { appRoot, env });
    expect(plan.dryRun).toBe(true);
    expect(existsSync(join(appRoot, 'rayfin', '.deployments.json'))).toBe(false);
  });

  it('never invokes up if silent auth returns the wrong identity', async () => {
    const execute = vi.fn();
    await expect(runRayfin('up', [], { appRoot, env, execute,
      acquireToken: async () => jwt({ tid: guid(30) }) })).rejects.toThrow('token tenant');
    expect(execute).not.toHaveBeenCalled();
  });

  it('supplies only the checked token to a mocked CLI and validates its receipt', async () => {
    const token = jwt();
    const inspectTarget = vi.fn(async () => undefined);
    const execute = vi.fn((_command, _args, options) => {
      expect(options.env.RAYFIN_TOKEN).toBe(token);
      registry({ 'marketing-demo': record() }, 'marketing-demo');
      return { status: 0 };
    });
    expect(await runRayfin('up', [], { appRoot, env, execute, acquireToken: async () => token, inspectTarget }))
      .toBe(0);
    expect(execute).toHaveBeenCalledOnce();
    expect(inspectTarget).toHaveBeenCalledOnce();
  });

  it('surfaces a CLI failure without claiming a deployment succeeded', async () => {
    expect(await runRayfin('up', [], { appRoot, env,
      execute: () => ({ status: 7 }), acquireToken: async () => jwt(),
      inspectTarget: async () => undefined })).toBe(7);
  });

  it('does not start the CLI when live target inspection fails', async () => {
    const execute = vi.fn();
    await expect(runRayfin('up', [], { appRoot, env, execute, acquireToken: async () => jwt(),
      inspectTarget: async () => { throw new Error('Workspace mismatch'); } }))
      .rejects.toThrow('Workspace mismatch');
    expect(execute).not.toHaveBeenCalled();
  });

  it.each([{ preferred_username: 'someone@example.invalid' }, { exp: 1 }, { exp: undefined }])(
    'rejects invalid token provenance %j', (claims) => {
      expect(() => checkToken(appProfile('production', { appRoot, env }), jwt(claims))).toThrow();
    });

  it('imports the wrapper without starting its entrypoint', async () => {
    vi.resetModules();
    const execute = vi.fn(() => { throw new Error('No commands during import'); });
    vi.doMock('node:child_process', () => ({ spawnSync: execute }));
    await import('../../scripts/rayfin-profile.mjs');
    expect(execute).not.toHaveBeenCalled();
    vi.doUnmock('node:child_process');
  });

  it('keeps the existing test and build:fabric scripts free of cloud preparation', () => {
    const scripts = JSON.parse(readFileSync(join(APP_ROOT, 'package.json'), 'utf8')).scripts;
    expect(scripts.test).toBe('vitest run');
    expect(scripts.pretest).toBeUndefined();
    expect(scripts['build:fabric']).toBe('tsc -b && vite build');
    expect(scripts['prebuild:fabric']).toBeUndefined();
    expect(scripts.prebuild).toContain('rayfin-profile.mjs env');
    expect(scripts.dev).toContain('rayfin-profile.mjs up --mode development');
  });
});
