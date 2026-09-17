import { existsSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual, parseEnv } from 'node:util';

export const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const PROFILE_ENV = 'FAB_MARKETING_PROFILE_DIR';
const require = createRequire(import.meta.url);

function object(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must contain an object.`);
  }
  return value;
}

function readJson(path) {
  return object(JSON.parse(readFileSync(path, 'utf8')), path);
}

function readYaml(path) {
  // Use the parser shipped with the installed CLI, not a second YAML dependency.
  const cliRequire = createRequire(require.resolve('@microsoft/rayfin-cli/package.json'));
  return object(cliRequire('yaml').parse(readFileSync(path, 'utf8')), path);
}

function text(value, label) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`${label} is required for the selected deployment profile.`);
  }
  return value;
}

function same(actual, expected, label) {
  if (actual !== expected) {
    throw new Error(`${label} does not match the selected deployment profile.`);
  }
}

function inside(path, directory) {
  const part = relative(realpathSync(directory), realpathSync(path));
  if (!part || part === '..' || part.startsWith(`..${sep}`) || isAbsolute(part)) {
    throw new Error(`${path} must stay inside ${directory}, without a legacy-path symlink.`);
  }
  return realpathSync(path);
}

function canonicalPath(path) {
  const missing = [];
  let existing = resolve(path);
  while (!existsSync(existing)) {
    const parent = dirname(existing);
    if (parent === existing) throw new Error(`Cannot resolve cache location: ${path}`);
    missing.unshift(basename(existing));
    existing = parent;
  }
  return resolve(realpathSync(existing), ...missing);
}

function containsPath(directory, path) {
  const part = relative(directory, path);
  return !part || (part !== '..' && !part.startsWith(`..${sep}`) && !isAbsolute(part));
}

function cachePath(value, label, env) {
  const expanded = text(value, label)
    .replace(/^~(?=[/\\]|$)/, homedir())
    .replace(/%([^%]+)%|\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z\d_]*)/g,
      (_match, windows, braced, bare) => text(env[windows || braced || bare], label));
  if (!isAbsolute(expanded)) throw new Error(`${label} must be an absolute cache path.`);
  return resolve(expanded);
}

function rayfinCacheDirectory(config, appRoot, profile, env) {
  const deployment = config.deployment ?? {};
  const azurePath = deployment.azure_config_dir === undefined ? null
    : cachePath(deployment.azure_config_dir, 'deployment.azure_config_dir', env);
  const azureDir = azurePath ? canonicalPath(azurePath) : null;
  let configuredCache;
  if (deployment.rayfin_config_dir !== undefined) {
    configuredCache = cachePath(deployment.rayfin_config_dir, 'deployment.rayfin_config_dir', env);
  } else {
    if (!azurePath) throw new Error('Configure deployment.rayfin_config_dir or deployment.azure_config_dir.');
    configuredCache = join(dirname(azurePath), '.rayfin');
  }
  const cacheDir = canonicalPath(configuredCache);
  const forbidden = [
    resolve(appRoot, '..'), profile, join(homedir(), '.rayfin'), join(homedir(), '.azure'),
    ...['OneDrive', 'OneDriveCommercial', 'OneDriveConsumer'].map((key) => env[key]).filter(Boolean),
  ].flatMap((path) => [resolve(path), canonicalPath(path)]);
  if ([configuredCache, cacheDir].some((path) =>
    forbidden.some((directory) => containsPath(directory, path))
      || path === canonicalPath(homedir())
      || path.split(/[/\\]/).some((part) => /^onedrive(?:\s*-\s*.+)?$/i.test(part)))) {
    throw new Error('Rayfin cache must be outside the repository, profile, OneDrive and normal/global caches.');
  }
  if (azureDir && (containsPath(azureDir, cacheDir) || containsPath(cacheDir, azureDir))) {
    throw new Error('Rayfin and Azure CLI caches must use separate, non-overlapping directories.');
  }
  if (existsSync(cacheDir) && !statSync(cacheDir).isDirectory()) {
    throw new Error('Rayfin cache location must be a directory.');
  }
  return cacheDir;
}

export function profileDirectory({ appRoot = APP_ROOT, env = process.env } = {}) {
  const repoRoot = resolve(appRoot, '..');
  let path;
  if (env[PROFILE_ENV] !== undefined) {
    path = resolve(repoRoot, text(env[PROFILE_ENV], PROFILE_ENV));
  } else {
    const pointerPath = join(repoRoot, 'deployments', 'active-profile.json');
    if (!existsSync(pointerPath)) return null;
    const name = readJson(pointerPath).profile;
    if (typeof name !== 'string' || !name.trim() || name === '.' || name === '..'
        || /[/\\]/.test(name)) {
      throw new Error('active-profile.json must select one named deployment profile.');
    }
    path = resolve(repoRoot, 'deployments', name);
  }
  const configPath = join(path, 'config.yaml');
  if (!existsSync(configPath) || !statSync(configPath).isFile()
      || realpathSync(path) === resolve(repoRoot, 'src')) {
    throw new Error(`Selected deployment profile has no separate config.yaml: ${path}`);
  }
  inside(configPath, path);
  return realpathSync(path);
}

export function appProfile(mode, { appRoot = APP_ROOT, env = process.env } = {}) {
  const profile = profileDirectory({ appRoot, env });
  if (!profile) return null;
  if (!/^[a-zA-Z0-9_-]+$/.test(mode)) throw new Error('Invalid Vite mode.');

  const config = readYaml(join(profile, 'config.yaml'));
  const authSource = config.deployment?.rayfin_auth_source ?? 'rayfin';
  if (!['rayfin', 'azure-cli'].includes(authSource)) {
    throw new Error('deployment.rayfin_auth_source must be rayfin or azure-cli.');
  }
  const statePath = inside(join(profile, 'state.json'), profile);
  const state = readJson(statePath);
  const context = {
    tenant_id: text(config.tenant_id, 'tenant_id'),
    subscription_id: text(config.az_subscription, 'az_subscription'),
    account: text(config.deployment?.expected_account, 'deployment.expected_account'),
    workspace_name: text(config.workspace_name, 'workspace_name'),
  };
  if (!isDeepStrictEqual(state._deployment_context, context)) {
    throw new Error('state.json does not belong to the selected deployment profile.');
  }

  const envDir = inside(join(profile, 'app'), profile);
  const envFile = inside(join(envDir, `.env.${mode}.local`), envDir);
  const values = parseEnv(readFileSync(envFile, 'utf8'));
  const expectedEnv = Object.fromEntries(
    Object.entries(values).filter(([key]) => key.startsWith('VITE_')),
  );
  const target = {
    tenantId: context.tenant_id,
    workspaceId: text(state.workspace_id, 'state.workspace_id'),
    workspaceName: context.workspace_name,
    account: context.account,
  };
  const bindings = {
    VITE_ENTRA_TENANT_ID: target.tenantId,
    VITE_DATA_WORKSPACE_ID: target.workspaceId,
    VITE_SEMANTIC_MODEL_ID: text(state.semantic_model_id, 'state.semantic_model_id'),
    VITE_FOUNDRY_ENDPOINT: text(state.foundry_project_endpoint, 'state.foundry_project_endpoint'),
    VITE_FOUNDRY_SUPERVISOR: text(state.foundry_supervisor_agent_name, 'state.foundry_supervisor_agent_name'),
    VITE_FOUNDRY_MODEL: text(config.foundry?.model_deployment, 'foundry.model_deployment'),
  };
  for (const [key, expected] of Object.entries(bindings)) {
    same(text(expectedEnv[key], `${envFile}: ${key}`), expected, key);
  }
  same(bindings.VITE_FOUNDRY_ENDPOINT,
    text(config.foundry?.project_endpoint, 'foundry.project_endpoint'),
    'Foundry project endpoint receipt');
  const clientId = text(expectedEnv.VITE_ENTRA_CLIENT_ID, `${envFile}: VITE_ENTRA_CLIENT_ID`);
  if (!/^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(clientId)
      || /^0{8}(?:-0{4}){3}-0{12}$/.test(clientId)) {
    throw new Error('VITE_ENTRA_CLIENT_ID must identify the new Entra SPA, not a placeholder.');
  }
  if (config.foundry?.supervisor?.agent_name) {
    same(bindings.VITE_FOUNDRY_SUPERVISOR, config.foundry.supervisor.agent_name,
      'Foundry supervisor receipt');
  }

  const legacyStatePath = join(appRoot, '..', 'src', 'state.json');
  if (existsSync(legacyStatePath)) {
    const legacy = readJson(legacyStatePath);
    for (const key of ['workspace_id', 'semantic_model_id']) {
      if (typeof legacy[key] === 'string' && legacy[key].toLowerCase() === state[key].toLowerCase()) {
        throw new Error(`Selected profile reuses legacy state.${key}.`);
      }
    }
  }
  const legacyEnvFiles = new Set([
    '.env', '.env.local', '.env.production.local', '.env.development.local', `.env.${mode}.local`,
  ]);
  for (const name of legacyEnvFiles) {
    const legacyEnvPath = join(appRoot, name);
    if (!existsSync(legacyEnvPath)) continue;
    const legacy = parseEnv(readFileSync(legacyEnvPath, 'utf8'));
    for (const key of ['VITE_ENTRA_CLIENT_ID', 'VITE_FOUNDRY_ENDPOINT']) {
      if (legacy[key] && legacy[key].replace(/\/+$/, '').toLowerCase()
          === expectedEnv[key].replace(/\/+$/, '').toLowerCase()) {
        throw new Error(`Selected profile reuses legacy ${key}.`);
      }
    }
  }

  for (const [key, value] of Object.entries(env)) {
    if (key.startsWith('VITE_') && value !== undefined) {
      same(value, expectedEnv[key], `Inherited ${key}`);
    }
  }
  const artifacts = join(profile, 'artifacts');
  const appArtifacts = join(artifacts, 'app-v2');
  const buildDir = join(appArtifacts, 'dist');
  for (const [path, parent] of [[artifacts, profile], [appArtifacts, artifacts], [buildDir, appArtifacts]]) {
    if (existsSync(path)) {
      inside(path, parent);
      if (!statSync(path).isDirectory()) throw new Error(`${path} must be a build output directory.`);
    }
  }
  if (env.FAB_MARKETING_APP_DIST !== undefined) {
    same(env.FAB_MARKETING_APP_DIST, buildDir, 'Inherited FAB_MARKETING_APP_DIST');
  }
  const captureFile = join(artifacts, 'captures', 'frozen_answers.json');
  let frozenAnswers = {};
  let capturePath = null;
  if (existsSync(captureFile)) {
    capturePath = inside(captureFile, artifacts);
    const capture = readJson(capturePath);
    if (!isDeepStrictEqual(capture.deployment, {
      tenantId: target.tenantId,
      workspaceId: state.workspace_id,
      semanticModelId: state.semantic_model_id,
      dataAgentId: text(state.data_agent_id, 'state.data_agent_id'),
      projectEndpoint: bindings.VITE_FOUNDRY_ENDPOINT,
      agentName: bindings.VITE_FOUNDRY_SUPERVISOR,
      model: bindings.VITE_FOUNDRY_MODEL,
    })) {
      throw new Error('Frozen answers do not belong to the selected deployment and data/Foundry bindings.');
    }
    frozenAnswers = object(capture.answers, 'Profile frozen answers');
  }
  const cacheDir = rayfinCacheDirectory(config, appRoot, profile, env);
  return { profile, appRoot, envDir, envFile, expectedEnv, target, state, buildDir, capturePath, frozenAnswers, cacheDir, authSource };
}

export function profileCapturePlugin(profile) {
  const source = resolve(profile.appRoot, 'src', 'data', 'frozen-answers.generated.json');
  return {
    name: 'marketing-profile-capture',
    enforce: 'pre',
    load(id) {
      if (resolve(id.replace(/[?#].*$/, '')) !== source) return null;
      if (!profile.capturePath) {
        this.warn('Selected profile has no frozen-answer capture; suggestions will use the live supervisor.');
      }
      // Provenance is checked locally, not published with the browser's answer dictionary.
      return JSON.stringify({ answers: profile.frozenAnswers });
    },
  };
}

export function validateViteEnv(profile, resolvedEnv) {
  if (!profile) return;
  if (!isDeepStrictEqual(resolvedEnv, profile.expectedEnv)) {
    throw new Error('Vite environment differs from the selected mode-local profile file; '
      + 'remove inherited or base-file VITE_* overrides.');
  }
}

export function workspaceKey(name) {
  return name.slice(0, 200).toLowerCase()
    .replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '')
    .replace(/-{2,}/g, '-').replace(/^-+|-+$/g, '');
}

export function readRegistry(appRoot = APP_ROOT) {
  const path = join(appRoot, 'rayfin', '.deployments.json');
  if (!existsSync(path)) return { deployments: {} };
  const registry = readJson(path);
  object(registry.deployments, 'Rayfin deployment registry');
  for (const [key, record] of Object.entries(registry.deployments)) {
    object(record, `Rayfin deployment ${key}`);
  }
  return registry;
}

export function checkReceipts(profile, registry) {
  const { target, expectedEnv } = profile;
  const key = workspaceKey(target.workspaceName);
  if (!key) throw new Error('Workspace name has no usable Rayfin registry key.');
  let selected;
  for (const [name, record] of Object.entries(registry.deployments)) {
    if (name !== key && record.fabricWorkspaceId !== target.workspaceId) continue;
    same(record.fabricWorkspaceId, target.workspaceId, `Rayfin receipt ${name}: workspace`);
    same(record.fabricTenantId, target.tenantId, `Rayfin receipt ${name}: tenant`);
    text(record.fabricItemId, `Rayfin receipt ${name}: fabricItemId`);
    if (selected && selected.fabricItemId !== record.fabricItemId) {
      throw new Error('More than one AppBackend receipt targets the selected workspace.');
    }
    selected = record;
  }
  const optionalBindings = {
    VITE_FABRIC_WORKSPACE_ID: target.workspaceId,
    VITE_FABRIC_TENANT_ID: target.tenantId,
    VITE_FABRIC_ITEM_ID: selected?.fabricItemId,
    VITE_RAYFIN_API_URL: selected?.fabricApiUrl,
    VITE_RAYFIN_PUBLISHABLE_KEY: selected?.publishableKey,
  };
  for (const [name, expected] of Object.entries(optionalBindings)) {
    if (expectedEnv[name] !== undefined) same(expectedEnv[name], expected, name);
  }
  if (expectedEnv.VITE_FABRIC_PORTAL_URL) {
    same(expectedEnv.VITE_FABRIC_PORTAL_URL, 'https://app.fabric.microsoft.com',
      'VITE_FABRIC_PORTAL_URL');
  }
  return selected;
}

export function checkAuth(profile, cacheDir) {
  same(canonicalPath(cacheDir), profile.cacheDir, 'Rayfin auth cache directory');
  const auth = readJson(inside(join(cacheDir, 'auth.json'), cacheDir));
  same(auth.identityType, 'user', 'Rayfin identity type');
  same(auth.tenantId?.toLowerCase(), profile.target.tenantId.toLowerCase(), 'Rayfin auth tenant');
  same(auth.userPrincipalName?.toLowerCase(), profile.target.account.toLowerCase(), 'Rayfin auth account');
  inside(join(cacheDir, 'cache.bin'), cacheDir);
}

export function planRayfin(command, args = [], {
  mode = 'production', appRoot = APP_ROOT, env = process.env,
} = {}) {
  if (command !== 'env' && command !== 'up') throw new Error('Only existing env/up workflows are supported.');
  const profile = appProfile(mode, { appRoot, env });
  if (!profile) {
    return { args: command === 'env' ? ['env', '--framework', 'vite'] : ['up', ...args],
      env: { ...env }, appRoot };
  }
  if (command === 'env') {
    return { args: null, env: { ...env }, appRoot, profile };
  }

  for (let index = 0; index < args.length; index++) {
    const arg = args[index];
    if (['--dry-run', '-n', '--yes', '-y', '--json'].includes(arg)) continue;
    if (arg === '--exclude-services' && args[++index] === 'staticHosting') continue;
    throw new Error(`Unsupported profile argument: ${arg}. Target overrides, subcommands and --force are not allowed.`);
  }
  const dryRun = args.includes('--dry-run') || args.includes('-n');
  const metadataFile = inside(join(profile.envDir, '.env'), profile.envDir);
  const metadata = parseEnv(readFileSync(metadataFile, 'utf8'));
  for (const name of Object.keys(metadata)) {
    if (!['RAYFIN_TENANT_ID', 'RAYFIN_WORKSPACE_ID'].includes(name)) {
      throw new Error(`Profile app/.env accepts only Rayfin tenant/workspace metadata, not ${name}.`);
    }
  }
  const cacheDir = profile.cacheDir;
  const childEnv = {
    ...env,
    ...profile.expectedEnv,
    [PROFILE_ENV]: profile.profile,
    FAB_MARKETING_APP_DIST: profile.buildDir,
    RAYFIN_CONFIG_DIR: cacheDir,
    RAYFIN_ENV_FILE: metadataFile,
    RAYFIN_TENANT_ID: profile.target.tenantId,
    RAYFIN_WORKSPACE_ID: profile.target.workspaceId,
  };
  same(metadata.RAYFIN_TENANT_ID, profile.target.tenantId, 'Rayfin metadata tenant');
  same(metadata.RAYFIN_WORKSPACE_ID, profile.target.workspaceId, 'Rayfin metadata workspace');
  for (const [name, value] of Object.entries(env)) {
    if (!name.startsWith('RAYFIN_') || value === undefined || value === '') continue;
    if (!['RAYFIN_CONFIG_DIR', 'RAYFIN_ENV_FILE', 'RAYFIN_TENANT_ID', 'RAYFIN_WORKSPACE_ID'].includes(name)) {
      throw new Error(`Inherited ${name} is not allowed for a deployment profile.`);
    }
    same(name === 'RAYFIN_CONFIG_DIR' ? cachePath(value, `Inherited ${name}`, env)
      : name.endsWith('_FILE') ? resolve(value) : value,
      childEnv[name], `Inherited ${name}`);
  }

  const templatePath = join(appRoot, 'rayfin', 'rayfin.yml');
  const templateSource = readFileSync(templatePath, 'utf8');
  const config = readYaml(templatePath);
  same(config.id, 'App-Customer-360', 'Rayfin app id');
  same(config.name, 'App-Customer-360', 'Rayfin app name');
  same(config.services?.staticHosting?.folder, '${FAB_MARKETING_APP_DIST:-dist}',
    'Rayfin staticHosting.folder');
  for (const service of ['data', 'storage', 'functions']) {
    same(config.services?.[service]?.enabled, false, `Rayfin ${service}.enabled`);
  }
  const registry = readRegistry(appRoot);
  checkReceipts(profile, registry);
  if (!dryRun && profile.authSource === 'rayfin') checkAuth(profile, cacheDir);
  return {
    args: ['up', '--tenant', profile.target.tenantId, '--workspace-id', profile.target.workspaceId,
      '--env-file', metadataFile, ...args],
    env: childEnv, appRoot, profile, registry, dryRun, cacheDir,
    templatePath, templateSource, templateConfig: config,
  };
}

export function restoreRayfinTemplate(plan) {
  if (!plan.profile || plan.dryRun) return;
  const currentSource = readFileSync(plan.templatePath, 'utf8');
  if (currentSource === plan.templateSource) return;
  const current = readYaml(plan.templatePath);
  const expected = structuredClone(plan.templateConfig);
  const folder = current.services?.staticHosting?.folder;
  if (![plan.profile.buildDir, expected.services.staticHosting.folder].includes(folder)) {
    throw new Error('Rayfin template changed unexpectedly; refusing to overwrite concurrent edits.');
  }
  expected.services.staticHosting.folder = folder;
  const registry = readRegistry(plan.appRoot);
  const receipt = checkReceipts(plan.profile, registry);
  const urls = current.services?.auth?.allowedRedirectUris;
  const originalUrls = expected.services.auth.allowedRedirectUris ?? [];
  if (!isDeepStrictEqual(urls, originalUrls)) {
    const hosted = receipt?.hostingUrl;
    if (!hosted || !isDeepStrictEqual(urls, [...originalUrls, hosted])) {
      throw new Error('Rayfin redirect template changed unexpectedly; inspect it before restoring.');
    }
    expected.services.auth.allowedRedirectUris = urls;
  }
  if (!isDeepStrictEqual(current, expected)) {
    throw new Error('Rayfin template has changes outside deployment output; refusing to overwrite them.');
  }
  // The CLI serializes resolved interpolation and the live URL into this tracked template.
  writeFileSync(plan.templatePath, plan.templateSource, 'utf8');
}

export function checkToken(profile, token) {
  const payload = token.split('.')[1];
  const claims = object(JSON.parse(Buffer.from(payload ?? '', 'base64url').toString('utf8')),
    'Rayfin token claims');
  same(claims.tid?.toLowerCase(), profile.target.tenantId.toLowerCase(), 'Rayfin token tenant');
  if (!['https://api.fabric.microsoft.com', 'https://api.fabric.microsoft.com/'].includes(claims.aud)) {
    throw new Error('Rayfin token audience must be the Fabric API.');
  }
  const account = claims.preferred_username || claims.upn || claims.unique_name;
  same(account?.toLowerCase(), profile.target.account.toLowerCase(), 'Rayfin token account');
  if (typeof claims.exp !== 'number' || claims.exp * 1000 <= Date.now() + 60_000) {
    throw new Error('Rayfin token is expired or lacks a usable expiry.');
  }
}

export function checkCompletedDeployment(plan) {
  const after = readRegistry(plan.appRoot);
  const selected = checkReceipts(plan.profile, after);
  if (!selected) throw new Error('Rayfin did not persist a receipt for the selected workspace.');
  same(after.active, workspaceKey(plan.profile.target.workspaceName), 'Active Rayfin receipt');
  for (const [key, record] of Object.entries(plan.registry.deployments)) {
    if (key === after.active) continue;
    if (!isDeepStrictEqual(after.deployments[key], record)) {
      throw new Error('Rayfin changed an unrelated deployment receipt; inspect the saved backup.');
    }
  }
  if (plan.profile.authSource === 'rayfin') checkAuth(plan.profile, plan.cacheDir);
}
