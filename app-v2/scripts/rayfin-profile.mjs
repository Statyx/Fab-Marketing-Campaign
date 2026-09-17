import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { checkCompletedDeployment, checkReceipts, checkToken, planRayfin, restoreRayfinTemplate } from './deployment-profile.mjs';

export async function inspectProfileTarget(plan, token, request = fetch) {
  const { target } = plan.profile;
  const base = `https://api.fabric.microsoft.com/v1/workspaces/${encodeURIComponent(target.workspaceId)}`;
  const headers = { Authorization: `Bearer ${token}` };
  const workspaceResponse = await request(base, { headers });
  if (!workspaceResponse.ok) {
    throw new Error(`Cannot inspect the selected Fabric workspace: HTTP ${workspaceResponse.status}.`);
  }
  const workspace = await workspaceResponse.json();
  if (workspace.id !== target.workspaceId || workspace.displayName !== target.workspaceName) {
    throw new Error('Live workspace differs from the profile; refusing a name-based Rayfin receipt lookup.');
  }
  const receipt = checkReceipts(plan.profile, plan.registry);
  if (receipt) {
    const response = await request(`${base}/items/${encodeURIComponent(receipt.fabricItemId)}`, { headers });
    if (!response.ok) throw new Error(`Cannot inspect the recorded AppBackend: HTTP ${response.status}.`);
    const item = await response.json();
    if (item.id !== receipt.fabricItemId || item.type !== 'AppBackend'
        || item.displayName !== 'App-Customer-360') {
      throw new Error('Recorded Rayfin item is not the expected App-Customer-360 AppBackend.');
    }
  }
}

export function acquireCliProfileToken(plan, execute = spawnSync) {
  const source = join(plan.appRoot, '..', 'src');
  const script = 'import sys;sys.path.insert(0,sys.argv[1]);'
    + 'from helpers import get_fabric_token;sys.stdout.write(get_fabric_token())';
  const result = execute('python', ['-c', script, source], {
    cwd: plan.appRoot, env: plan.env, encoding: 'utf8', timeout: 180_000,
  });
  if (result.error || result.status !== 0 || !result.stdout?.trim()) {
    throw new Error('Checked Azure CLI token acquisition failed; confirm the selected profile login. '
      + (result.stderr?.trim().slice(-1000) || result.error?.message || 'No token returned.'));
  }
  const token = result.stdout.trim();
  checkToken(plan.profile, token);
  return token;
}

async function acquireProfileToken(plan) {
  if (plan.profile.authSource === 'azure-cli') return acquireCliProfileToken(plan);
  // The CLI snapshots RAYFIN_CONFIG_DIR when its auth module loads.
  Object.assign(process.env, plan.env);
  const { RayfinAuth } = await import('@microsoft/rayfin-cli/auth');
  const auth = new RayfinAuth({ tenantId: plan.profile.target.tenantId });
  const result = await auth.acquireToken(undefined, { silentOnly: true });
  return result.token;
}

export async function runRayfin(command, args = [], options = {}) {
  const { execute = spawnSync, acquireToken = acquireProfileToken,
    inspectTarget = inspectProfileTarget, ...selection } = options;
  const plan = planRayfin(command, args, selection);
  if (!plan.args) return 0;
  if (plan.profile && !plan.dryRun) {
    const token = await acquireToken(plan);
    checkToken(plan.profile, token);
    await inspectTarget(plan, token);
    // A checked, silently acquired token prevents `up` opening an account picker mid-deploy.
    plan.env.RAYFIN_TOKEN = token;
  }
  const require = createRequire(import.meta.url);
  const cliRoot = dirname(require.resolve('@microsoft/rayfin-cli/package.json'));
  let result;
  try {
    result = execute(process.execPath, [join(cliRoot, 'scripts', 'main'), ...plan.args], {
      cwd: plan.appRoot, env: plan.env, stdio: 'inherit',
    });
  } finally {
    restoreRayfinTemplate(plan);
  }
  if (result.error) throw result.error;
  if (result.signal) throw new Error(`Rayfin was terminated by ${result.signal}.`);
  if (result.status === 0 && plan.profile && !plan.dryRun) checkCompletedDeployment(plan);
  return result.status ?? 1;
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  const modeIndex = args.indexOf('--mode');
  let mode = 'production';
  if (modeIndex !== -1) {
    mode = args[modeIndex + 1];
    if (!mode || mode.startsWith('-')) throw new Error('--mode requires a Vite mode name.');
    args.splice(modeIndex, 2);
  }
  return runRayfin(command, args, { mode });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then((code) => {
    process.exitCode = code;
  }).catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
